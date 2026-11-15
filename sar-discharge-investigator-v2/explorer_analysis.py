"""Run selected NISAR measurements and serve their short-lived map assets."""
from collections import OrderedDict
from datetime import datetime, timezone
from hashlib import sha256
from threading import BoundedSemaphore, Lock
from time import monotonic
from uuid import uuid4

from explorer_catalogue import POLARIZATIONS
from nisar_tool.errors import ApiError
from runtime_measurement import RuntimeMeasurement
from runtime_recognition import RuntimeRecognition


DATE_COUNTS = {"change": (2, 2), "time_series": (2, 6), "wetland": (1, 1), "oil": (1, 1)}
RESULT_TTL = 1800
MAX_ASSET_CACHE_BYTES = 64_000_000
ASSET_FORMATS = {
    "image": ("sar", "png", "image/png", (b"\x89PNG\r\n\x1a\n",)),
    "science": ("science", "tif", "image/tiff", (b"II*\0", b"MM\0*")),
}


class ExplorerAnalysis:
    def __init__(self, app_dir, catalogue):
        self.catalogue = catalogue
        self.measurement = RuntimeMeasurement(app_dir / "data", catalogue.seed)
        self.recognition = RuntimeRecognition(app_dir, catalogue.seed)
        self._execution = BoundedSemaphore(1)
        self._lock = Lock()
        self._visitors = OrderedDict()
        self._runs = OrderedDict()
        self._assets = {}
        self._asset_bytes = 0

    def model_observations(self):
        return {mode: [ident for ident, row in self.recognition.manifest["rows"].items()
                       if row["mode"] == mode and ident in self.catalogue.seed]
                for mode in ("wetland", "oil")}

    def _throttle(self, visitor):
        now = monotonic()
        with self._lock:
            started, count = self._visitors.get(visitor, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            if count >= 10:
                raise ApiError("analysis_rate_limit", "Wait a minute before running another analysis.", 429)
            self._visitors[visitor] = (started, count + 1)
            self._visitors.move_to_end(visitor)
            while len(self._visitors) > 128:
                self._visitors.popitem(last=False)

    def _discard_oldest(self):
        _, (_, size, paths) = self._runs.popitem(last=False)
        for path in paths:
            del self._assets[path]
        self._asset_bytes -= size

    def _expire(self, now):
        while self._runs and next(iter(self._runs.values()))[0] <= now:
            self._discard_oldest()

    def _remember(self, result_id, layer, artifacts):
        if not artifacts:
            return
        total = sum(len(content) for content in artifacts.values())
        if total > MAX_ASSET_CACHE_BYTES:
            raise ApiError("analysis_output_too_large", "The generated files exceed the server's size limit.", 422)
        entries = {}
        for name, content in artifacts.items():
            folder, ext, media_type, signatures = ASSET_FORMATS[name]
            if not content.startswith(signatures):
                raise ValueError("Unexpected generated raster format.")
            filename = f"nisar-{result_id}.{ext}"
            path = f"/{folder}/{filename}"
            layer.update({f"{name}_url": path, f"{name}_size_bytes": len(content),
                          f"{name}_sha256": sha256(content).hexdigest()})
            entries[path] = (content, media_type, filename if name == "science" else None)
        with self._lock:
            now = monotonic()
            self._expire(now)
            while self._runs and (self._asset_bytes + total > MAX_ASSET_CACHE_BYTES or len(self._runs) >= 12):
                self._discard_oldest()
            self._runs[result_id] = (now + RESULT_TTL, total, tuple(entries))
            self._assets.update(entries)
            self._asset_bytes += total

    def asset(self, path):
        with self._lock:
            self._expire(monotonic())
            return self._assets.get(path)

    def _selection(self, body):
        if not isinstance(body, dict) or set(body) != {"kind", "site_id", "polarization", "observation_ids"}:
            raise ApiError("invalid_analysis", "Choose an analysis, site, channel and acquisition dates.")
        kind, site_id, pol, ids = (body[key] for key in ("kind", "site_id", "polarization", "observation_ids"))
        if not isinstance(kind, str) or kind not in DATE_COUNTS:
            raise ApiError("invalid_analysis_kind", "Choose change, time series, wetland or oil screening.")
        if not isinstance(site_id, str) or site_id not in self.catalogue.sites:
            raise ApiError("invalid_analysis_site", "Choose a registered study area.")
        if not isinstance(pol, str) or pol not in POLARIZATIONS:
            raise ApiError("invalid_polarization", "Choose HH, HV, VH or VV.")
        minimum, maximum = DATE_COUNTS[kind]
        if (not isinstance(ids, list) or not minimum <= len(ids) <= maximum
                or any(not isinstance(item, str) or not 0 < len(item) <= 200 for item in ids) or len(set(ids)) != len(ids)):
            choices = f"{minimum} to {maximum}" if minimum != maximum else str(minimum)
            raise ApiError("invalid_analysis_dates", f"Choose {choices} distinct acquisition dates for this analysis.")
        rows = [self.catalogue.seed.get(item) for item in ids]
        if any(row and row.get("site_id") != site_id for row in rows):
            raise ApiError("invalid_analysis_site", "Choose acquisitions within the selected study area.")
        return kind, site_id, pol, ids, rows

    def run(self, body, visitor):
        kind, site_id, pol, ids, rows = self._selection(body)
        result = {"kind": kind, "site_id": site_id, "polarization": pol, "observation_ids": ids}
        if not all(rows):
            return {**result, "status": "unavailable", "reason": "observation_not_registered",
                    "message": "This analysis needs a calibrated NISAR raster prepared for each date. The selected catalogue entry has no prepared pixels."}
        try:
            ordered_ids = [row["id"] for row in sorted(rows, key=lambda row: datetime.fromisoformat(row["acquired_at"]))]
        except (KeyError, TypeError, ValueError):
            return {**result, "status": "unavailable", "reason": "acquisition_time_unknown",
                    "message": "A registered acquisition has no usable date and time."}
        if not self._execution.acquire(blocking=False):
            raise ApiError("analysis_busy", "The server is analyzing another selection. Try again in a moment.", 429)
        try:
            self._throttle(visitor)
            result["observation_ids"] = ordered_ids
            if kind == "change":
                measured = self.measurement.change(*ordered_ids, pol)
            elif kind == "time_series":
                measured = self.measurement.time_series(site_id, pol, ordered_ids)
            else:
                measured = self.recognition.run(site_id, ordered_ids[0], kind, pol)
            return self._publish(result, measured)
        finally:
            self._execution.release()

    def _publish(self, result, measured):
        artifacts = measured.pop("_artifacts", {})
        result.update(measured)
        if "counts" in result:
            result["metrics"] = result["counts"]
        if result["status"] != "measured":
            result.setdefault("message", result.get("summary") or "This selection has insufficient data for the requested analysis.")
            return result
        result_id = "analysis-" + uuid4().hex
        result.update(analysis_id=result_id, runtime_computed=True,
                      created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        kind = result["kind"]
        if kind == "change":
            layer = result["change_layer"]
            result["metrics"] = result["comparison"]
            result["message"] = layer["note"]
        elif kind == "time_series":
            layer = result["time_series"]
            result["metrics"] = {key: result[key] for key in ("common_pixels", "grid_pixels", "common_fraction")}
            result["message"] = layer["summary"]
        else:
            layer = result.get("recognition_layer")
            result.setdefault("message", result.get("summary") or "NISAR model screening completed.")
        if layer is not None:
            layer.update(id=result_id, analysis_id=result_id)
            self._remember(result_id, layer, artifacts)
        return result
