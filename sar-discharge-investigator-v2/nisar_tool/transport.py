"""Bounded public HTTP reads, with an allowlist and shared transfer accounting.

This transport intentionally has no Earthdata credential or cookie support.
Scientific HDF5 files cannot pass its URL allowlist.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import threading
import os
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests

from .errors import ApiError


@dataclass
class FetchResult:
    content: bytes
    headers: dict
    status_code: int
    url: str


class PublicTransport:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._reserved = 0
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_url(url, *, cdn_suffix=None):
        try:
            p = urlsplit(url)
            port = p.port
        except (ValueError, TypeError):
            raise ApiError("unsafe_url", "Invalid provider URL.") from None
        if (p.scheme != "https" or port not in (None, 443) or p.username or
                p.password or p.fragment or "\\" in url or any(ord(c) < 32 for c in url)):
            raise ApiError("unsafe_url", "Only public HTTPS provider URLs are allowed.")
        host = p.hostname
        signed_browse_cdn = False
        if host == "api.daac.asf.alaska.edu":
            permitted = p.path.rstrip("/") == "/services/search/param"
        elif host == "nisar.asf.earthdatacloud.nasa.gov":
            permitted = p.path.startswith("/BROWSE/") and p.path.lower().endswith((".png", ".jpg", ".jpeg")) and not p.query
        elif host == "harmony.earthdata.nasa.gov":
            permitted = p.path.rstrip("/") == "/capabilities"
        elif host == "cmr.earthdata.nasa.gov":
            permitted = p.path in ("/search/granules.json", "/search/collections.json")
        elif host in ("en.wikipedia.org", "simple.wikipedia.org"):
            permitted = p.path == "/w/api.php"
        elif host == "d1mv8zhcvry6x4.cloudfront.net" and cdn_suffix:
            # Public ASF browse endpoint issues an anonymous, short-lived signed
            # CDN redirect. Accept only the observed browse bucket and exactly
            # the asset requested at the original ASF URL; never persist query.
            permitted = bool(re.fullmatch(
                r"/s3-[0-9a-f]{32}/sds-n-cumulus-prod-nisar-browse\.s3\.us-west-2\.amazonaws\.com/.+\.(png|jpg|jpeg)",
                p.path, re.IGNORECASE)) and p.path.endswith("/" + cdn_suffix)
            signed_browse_cdn = permitted and set(parse_qs(p.query)).issubset(
                {"A-userid", "Expires", "Signature", "Key-Pair-Id", "Policy"})
            permitted = permitted and signed_browse_cdn
        else:
            permitted = False
        # No encoded path traversal, credential query strings, or science files.
        if not permitted or any(x in p.path.lower() for x in ("..", "%2e", "%2f", "%5c")):
            raise ApiError("unsafe_url", "This endpoint is outside the public data allowlist.")
        if p.query and not signed_browse_cdn:
            raise ApiError("unsafe_url", "Pass public search parameters separately from the provider URL.")
        return url

    @staticmethod
    def _ledger_total(path):
        if path is None or not path.exists():
            return 0
        total = 0
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    total += int(row.get("body_bytes", 0)) + int(row.get("estimated_extra_bytes", 0))
        except (OSError, ValueError, TypeError):
            raise ApiError("ledger_error", "Transfer ledger is unreadable; no new transfer was started.", 503) from None
        return total

    def budget(self):
        with self._lock:
            baseline = self._ledger_total(self.settings.baseline_ledger)
            tool = self._ledger_total(self.settings.ledger_path)
            return {"limit_bytes": self.settings.network_limit_bytes,
                    "prior_workflow_accounted_bytes": baseline,
                    "api_accounted_bytes": tool,
                    "remaining_bytes": max(0, self.settings.network_limit_bytes - baseline - tool - self._reserved),
                    "accounting": "Response bodies measured; 64 KiB per attempt reserved for overhead/refused bodies. Not a host-wide wire meter."}

    @contextmanager
    def _process_lock(self):
        # CLI and server processes share one ledger. Reserve/check/append as one
        # operation so simultaneous processes cannot each spend the same budget.
        path = self.settings.state_dir / ".transport.lock"
        with path.open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + self.settings.timeout_seconds + 10
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ApiError("transport_busy", "Another local transfer is still running; retry shortly.", 503) from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def get_bytes(self, url, params=None, cap=8_000_000, purpose="public metadata"):
        with self._lock, self._process_lock():
            return self._fetch(url, params=params, cap=cap, purpose=purpose)

    def _fetch(self, url, params=None, cap=8_000_000, purpose="public metadata"):
        self.validate_url(url)
        if isinstance(cap, bool) or not isinstance(cap, int) or not 0 < cap <= self.settings.max_response_bytes:
            raise ApiError("invalid_cap", "Response cap must be within the configured maximum.")
        reservation = cap + 4 * 65_536 + 16_384
        with self._lock:
            if self.budget()["remaining_bytes"] < reservation:
                raise ApiError("network_budget_exceeded", "The shared run transfer budget cannot cover this request.", 429,
                               self.budget())
            self._reserved += reservation
        received, status, redirects = 0, None, 0
        final_url = url
        original = urlsplit(url)
        cdn_suffix = original.path[len("/BROWSE/"):] if (
            original.hostname == "nisar.asf.earthdatacloud.nasa.gov" and original.path.startswith("/BROWSE/")) else None
        try:
            # Disable implicit .netrc, proxy credentials, ambient sessions and cookies.
            with requests.Session() as session:
                session.trust_env = False
                agent = ("SARtainty/1.0 (https://github.com/YoanTrendafilov2002/NASA-Space-Apps---Relax-Zone; public definition lookup)"
                         if original.hostname in ("en.wikipedia.org", "simple.wikipedia.org") else "Sofia-NISAR-API/1.0 (public browse client)")
                session.headers.update({"User-Agent": agent,
                                        "Accept-Encoding": "identity"})
                current, current_params = url, params
                while True:
                    self.validate_url(current, cdn_suffix=cdn_suffix)
                    session.cookies.clear()
                    try:
                        response = session.get(current, params=current_params, stream=True,
                                               allow_redirects=False,
                                               timeout=(8, self.settings.timeout_seconds))
                    except requests.RequestException:
                        raise ApiError("provider_unavailable", "The provider could not be reached; retry the request later.", 502) from None
                    with response:
                        status = response.status_code
                        final_url = response.url
                        if status in (301, 302, 303, 307, 308):
                            redirects += 1
                            if redirects > 3:
                                raise ApiError("provider_redirect", "The provider returned too many redirects.", 502)
                            location = response.headers.get("Location")
                            if not location:
                                raise ApiError("provider_redirect", "The provider returned a redirect without a location.", 502)
                            current = urljoin(current, location)
                            current_params = None
                            # Next iteration checks target before issuing the request.
                            continue
                        if status != 200:
                            code = "provider_auth_required" if status in (401, 403) else "provider_error"
                            raise ApiError(code, f"The public provider returned HTTP {status}.", 502,
                                           {"provider_status": status})
                        length = response.headers.get("Content-Length")
                        if length is not None:
                            try:
                                length = int(length)
                                if length < 0:
                                    raise ValueError
                            except ValueError:
                                raise ApiError("invalid_provider_response", "Invalid provider content length.", 502) from None
                            if length > cap:
                                raise ApiError("response_too_large", "The provider response exceeds the per-request byte limit.", 413)
                        pieces = []
                        try:
                            for piece in response.iter_content(chunk_size=16_384):
                                received += len(piece)
                                if received > cap:
                                    raise ApiError("response_too_large", "The provider response exceeded the streaming byte limit.", 413)
                                pieces.append(piece)
                        except requests.RequestException:
                            raise ApiError("provider_unavailable", "The provider transfer did not complete.", 502) from None
                        if length is not None and not response.headers.get("Content-Encoding") and received != length:
                            raise ApiError("invalid_provider_response", "The provider response length did not match its headers.", 502)
                        public_final_url = urlunsplit((*urlsplit(final_url)[:3], "", ""))
                        return FetchResult(b"".join(pieces), dict(response.headers), status, public_final_url)
        finally:
            row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "method": "GET",
                   "url": urlunsplit((*urlsplit(url)[:3], "", "")),
                   "purpose": purpose, "status": status, "body_bytes": received,
                   "estimated_extra_bytes": 65_536 * min(4, redirects + 1), "redirect_count": redirects}
            with self._lock:
                self._reserved -= reservation
                with self.settings.ledger_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=True) + "\n")

    def get_json(self, url, params=None, cap=8_000_000, purpose="public metadata"):
        result = self.get_bytes(url, params=params, cap=cap, purpose=purpose)
        try:
            return json.loads(result.content)
        except (ValueError, UnicodeError):
            raise ApiError("invalid_provider_response", "The provider did not return valid JSON.", 502) from None
