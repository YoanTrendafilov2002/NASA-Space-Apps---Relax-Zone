"""Small loopback-only HTTP interface for the NISAR metadata/browse client."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
import zipfile

from .errors import ApiError


ALLOWED_ORIGINS = {"http://127.0.0.1:4173", "http://localhost:4173"}
MAX_BODY_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
SEARCH_KEYS = {"bbox", "start", "end", "product", "limit", "previews"}


def validate_public_origin(value: str | None) -> str | None:
    """Accept one exact HTTPS DNS origin, never credentials, a path, or a wildcard."""
    if value is None or value == "":
        return None
    message = "Public origin must be one exact HTTPS hostname, without credentials, a port, a path, query parameters, or wildcards."
    if not isinstance(value, str) or len(value) > 300 or not re.fullmatch(r"https://[A-Za-z0-9.-]+/?", value, flags=re.IGNORECASE):
        raise ApiError("invalid_public_origin", message)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        valid_hostname = bool(hostname) and len(hostname) <= 253 and "." in hostname and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in hostname.split(".")
        )
        if (
            parsed.scheme != "https" or not valid_hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or parsed.netloc.lower() != hostname.lower()
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ApiError("invalid_public_origin", message) from None
    return "https://" + hostname.lower()


def search_parameters(values: dict) -> dict:
    """Normalize the same compact search request for HTTP and CLI callers."""
    if not isinstance(values, dict):
        raise ApiError("invalid_request", "Search parameters must be a JSON object.")
    unknown = sorted(set(values) - SEARCH_KEYS)
    if unknown:
        raise ApiError("invalid_parameter", "Unrecognized search parameter.", details={"parameters": unknown})
    bbox = values.get("bbox")
    if isinstance(bbox, str):
        bbox = bbox.split(",")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ApiError("invalid_bbox", "bbox must contain west,south,east,north.")
    try:
        if any(isinstance(v, bool) for v in bbox):
            raise ValueError
        bbox = [float(v) for v in bbox]
    except (TypeError, ValueError, OverflowError):
        raise ApiError("invalid_bbox", "bbox coordinates must be finite numbers.") from None
    west, south, east, north = bbox
    if not all(math.isfinite(v) for v in bbox) or not (
        -180 <= west < east <= 180 and -90 <= south < north <= 90
    ):
        raise ApiError("invalid_bbox", "bbox must satisfy -180 <= west < east <= 180 and -90 <= south < north <= 90.")
    limit = values.get("limit", 5)
    try:
        if isinstance(limit, bool) or isinstance(limit, float) and not limit.is_integer():
            raise ValueError
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        raise ApiError("invalid_limit", "limit must be an integer from 1 to 20.") from None
    if not 1 <= limit <= 20:
        raise ApiError("invalid_limit", "limit must be an integer from 1 to 20.")
    previews = values.get("previews", "links")
    if not isinstance(previews, str) or previews not in {"none", "links", "inline"}:
        raise ApiError("invalid_previews", "previews must be none, links, or inline.")
    product = values.get("product", "GCOV")
    if not isinstance(product, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", product):
        raise ApiError("invalid_product", "product must be a NISAR product type such as GCOV.")
    result = {"bbox": bbox, "product": product.upper(), "limit": limit, "previews": previews}
    for key in ("start", "end"):
        value = values.get(key)
        if value not in (None, ""):
            if not isinstance(value, str) or len(value) > 80:
                raise ApiError("invalid_date", f"{key} must be an ISO date or timestamp.")
            result[key] = value
    return result


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def build_bundle(client, values: dict) -> bytes:
    """Bundle metadata and separate provider browse images, never science assets."""
    params = search_parameters(values)
    params["previews"] = "links"
    result = copy.deepcopy(client.search(**params))
    advertised_bytes = sum(
        entry["size_bytes"]
        for item in result.get("items", []) for entry in item.get("previews", [])
        if isinstance(entry.get("size_bytes"), int) and entry["size_bytes"] > 0
    )
    if advertised_bytes > MAX_BUNDLE_BYTES:
        raise ApiError("bundle_too_large", "Advertised previews exceed the 128 MiB bundle limit. Reduce the search limit.", status=413)
    files = []
    used = 0
    for item_index, item in enumerate(result.get("items", [])):
        for entry in item.get("previews", []):
            preview = client.preview(item["id"], index=entry["index"])
            content = preview["content"]
            if not isinstance(content, bytes):
                raise ApiError("invalid_preview", "The provider preview did not contain binary image data.", status=502)
            used += len(content)
            if used > MAX_BUNDLE_BYTES:
                raise ApiError("bundle_too_large", "The preview bundle exceeds the 128 MiB limit. Reduce the search limit.", status=413)
            # Generate names locally: provider filenames cannot escape the ZIP directory.
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "image/tiff": ".tif"}.get(preview["media_type"], ".img")
            path = f"previews/{item_index + 1:02d}-{int(entry['index']):02d}{extension}"
            entry.pop("data_uri", None)
            entry.update({
                "file": path,
                "media_type": preview["media_type"],
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "width": preview.get("width"),
                "height": preview.get("height"),
                "source_url": preview["source_url"],
                "scientific": False,
            })
            files.append((path, content))
    manifest = {
        "format": "NISARBrowseBundle", "version": 1, "scientific": False,
        "description": "Search metadata and provider browse images. No scientific radar rasters are included.",
        "preview_count": len(files), "preview_bytes": used, "search": result,
    }
    manifest_bytes = _json_bytes(manifest)
    if used + len(manifest_bytes) > MAX_BUNDLE_BYTES:
        raise ApiError("bundle_too_large", "The preview bundle exceeds the 128 MiB limit. Reduce the search limit.", status=413)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        for filename, content in files:
            archive.writestr(filename, content)
    if output.tell() > MAX_BUNDLE_BYTES:
        raise ApiError("bundle_too_large", "The preview bundle exceeds the 128 MiB limit. Reduce the search limit.", status=413)
    return output.getvalue()


HELP_HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NISAR search and browse API</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;background:#f5f7fa;color:#142538;margin:0}main{max-width:1000px;margin:auto;padding:28px}h1{margin-bottom:6px}p{max-width:80ch}form{background:white;padding:18px;border:1px solid #ccd5df;border-radius:8px;display:flex;gap:12px;flex-wrap:wrap}label{display:flex;flex-direction:column;font-size:14px;gap:4px}input,select,button{font:inherit;padding:8px;border:1px solid #9babbc;border-radius:4px}input[name=bbox]{width:290px}button{background:#123f6c;color:white;cursor:pointer;align-self:end}button:disabled{opacity:.6}code,pre{background:#e8edf3;border-radius:4px;padding:3px 6px;overflow:auto}#results{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}article{background:white;border:1px solid #ccd5df;border-radius:8px;padding:16px;min-width:0}article h3{font-size:15px;overflow-wrap:anywhere}article img{max-width:100%;height:210px;object-fit:contain;background:#e8edf3}small{display:block;overflow-wrap:anywhere}a{color:#075aa0}.note{border-left:4px solid #da9827;padding-left:12px}#status{white-space:pre-wrap}
</style><main>
<h1>NISAR search and browse API</h1>
<p>Discover NISAR products and inspect provider preview images. This helper searches metadata; previews are illustrations and are <strong>not calibrated scientific radar data</strong>. Science downloads may require NASA Earthdata access.</p>
<p>Provider sign-in belongs to the operator's browser session. This API and its public tunnel do not inherit Earthdata, JAXA or Canadian account sessions. Open an Earthdata subset request in the browser where you signed in.</p>
<form id="search"><label>Bounding box: west,south,east,north<input name="bbox" value="23.29,42.67,23.36,42.73" required></label><label>Product<select name="product"><option>GCOV</option><option>GUNW</option><option>GOFF</option><option>SME2</option><option value="GSLC">GSLC (complex; metadata only)</option><option value="RSLC">RSLC (complex; metadata only)</option></select></label><label>Start date<input type="date" name="start"></label><label>End date<input type="date" name="end"></label><label>Results<input name="limit" type="number" min="1" max="20" value="1" style="width:70px"></label><button>Search</button></form>
<p id="status" role="status" aria-live="polite">Enter an area and optional acquisition dates, then search.</p><p><a id="bundle" hidden>Download these results and separate previews as ZIP</a></p><section id="results" aria-label="Product results"></section>
<h2>Endpoints</h2><ul><li><code>GET /health</code></li><li><code>GET /api/search?bbox=23.29,42.67,23.36,42.73&amp;product=GCOV&amp;limit=3&amp;previews=links</code></li><li><code>POST /api/search</code> with the same parameters as JSON</li><li><code>GET /api/products/{id}</code></li><li><code>GET /api/products/{id}/previews/{index}</code> returns one image</li><li><code>GET /api/bundle</code> with search parameters returns a ZIP manifest and image files</li><li><code>POST /api/subset-plan</code> with JSON <code>{"product_id":"…","bbox":[west,south,east,north]}</code> prepares a browser handoff</li></ul>
<p><code>previews=none</code> returns metadata only; <code>links</code> returns individual image endpoints; <code>inline</code> includes all advertised previews as image data URIs with a 32 MB raw-image cap. ZIP bundles have a 128 MiB cap.</p>
<p class="note">No cloud processing or paid service is started by this interface. A Harmony subset plan is a separate reviewable handoff.</p>
<script>
const form=document.querySelector('form'), status=document.querySelector('#status'), results=document.querySelector('#results'), bundle=document.querySelector('#bundle');
function append(parent,tag,text){const el=document.createElement(tag);el.textContent=text;parent.append(el);return el;}
function showPreview(parent,item,preview){const image=document.createElement('img');image.src='/api/products/'+encodeURIComponent(item.id)+'/previews/'+encodeURIComponent(preview.index);image.alt='Provider browse image '+preview.index+' for '+item.id;image.loading='lazy';image.addEventListener('error',()=>{image.remove();append(parent,'p','This provider preview is unavailable.');});parent.append(image);append(parent,'small','Provider browse; not calibrated science data.');}
form.addEventListener('submit',async event=>{event.preventDefault();const query=new URLSearchParams(new FormData(form));for(const [key,value] of [...query])if(!value)query.delete(key);query.set('previews','links');results.replaceChildren();bundle.hidden=true;status.textContent='Searching ASF…';form.querySelector('button').disabled=true;
try{const response=await fetch('/api/search?'+query), data=await response.json();if(!response.ok)throw Error(data.error?.message||'Search failed.');status.textContent=`${data.count??data.items?.length??0} products returned. Preview images are not scientific measurements.`;bundle.href='/api/bundle?'+query;bundle.hidden=false;
for(const item of data.items||[]){const card=append(results,'article','');append(card,'h3',item.id);append(card,'p',`${item.product_type||''} · ${item.acquisition_start||'Date unavailable'}`);const detail=append(card,'a','Product metadata');detail.href='/api/products/'+encodeURIComponent(item.id);detail.target='_blank';detail.rel='noopener';
const prepare=append(card,'button','Prepare science subset');prepare.type='button';prepare.disabled=['GSLC','RSLC'].includes(item.product_type);prepare.addEventListener('click',async()=>{prepare.disabled=true;const feedback=append(card,'p','Preparing a browser handoff…');try{const response=await fetch('/api/subset-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:item.id,bbox:query.get('bbox').split(',').map(Number)})}),plan=await response.json();if(!response.ok)throw Error(plan.error?.message||'Could not prepare the subset.');feedback.textContent="Plan prepared; no job submitted. Review it first, then open it in the operator's signed-in Earthdata browser. Opening the request starts Harmony processing; output size is not yet known.";const link=append(card,'a','Inspect subset plan JSON');const blob=new Blob([JSON.stringify(plan,null,2)],{type:'application/json'});link.href=URL.createObjectURL(blob);link.download='nisar-subset-plan.json';const browserUrl=plan.browser_request_url||plan.authorization_url||plan.browser_url||plan.harmony_url||plan.request_url;if(browserUrl&&/^https:\/\//.test(browserUrl)){const open=append(card,'a',"Submit in the operator's Earthdata browser");open.href=browserUrl;open.target='_blank';open.rel='noopener';}}catch(error){feedback.textContent=error.message||'Could not prepare the subset.';}finally{prepare.disabled=false;}});
const previews=item.previews||[];if(previews.length){const first=previews.find(p=>/thumb/i.test(p.kind||'')||/thumb/i.test(p.source_url||''))||previews[0];showPreview(card,item,first);const others=previews.filter(p=>p!==first);if(others.length){const details=append(card,'details','');append(details,'summary',`Show ${others.length} additional provider previews`);details.addEventListener('toggle',()=>{if(details.open&&!details.dataset.loaded){details.dataset.loaded='yes';for(const preview of others)showPreview(details,item,preview);}});}}
if(!item.previews?.length)append(card,'p',item.preview_unavailable_reason||'No provider preview is listed.');}}
catch(error){status.textContent=error.message||'The request could not be completed.';}finally{form.querySelector('button').disabled=false;}});
</script></main></html>'''


class NisarHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, client, public_origin=None):
        self.client = client
        self.public_origin = public_origin
        self.allowed_origins = frozenset(ALLOWED_ORIGINS | ({public_origin} if public_origin else set()))
        super().__init__(address, NisarHandler)


class NisarHandler(BaseHTTPRequestHandler):
    server_version = "NisarLocalAPI/1"
    sys_version = ""

    def _send(self, status, content: bytes, media_type="application/json; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if filename:
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:180] or "download"
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status, value):
        self._send(status, _json_bytes(value))

    def _check_origin(self):
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed_hosts:
            raise ApiError("host_not_allowed", "Use this API through its localhost or 127.0.0.1 address.", status=403)
        origin = self.headers.get("Origin")
        own_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if origin and origin not in self.server.allowed_origins | own_origins:
            raise ApiError("origin_not_allowed", "This local API does not allow requests from that origin.", status=403)

    def _query(self):
        try:
            fields = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=30)
        except ValueError:
            raise ApiError("invalid_parameter", "Too many query parameters.") from None
        if any(len(values) != 1 for values in fields.values()):
            raise ApiError("invalid_parameter", "Each query parameter may be specified only once.")
        return {key: values[0] for key, values in fields.items()}

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError("invalid_content_type", "This endpoint requires application/json.", status=415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("invalid_body", "Content-Length must be a valid integer.") from None
        if size < 1 or size > MAX_BODY_BYTES:
            raise ApiError("invalid_body", "JSON body must be between 1 byte and 1 MiB.", status=413 if size > MAX_BODY_BYTES else 400)
        try:
            return json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            raise ApiError("invalid_json", "Request body must contain valid JSON.") from None

    def _handle(self, method):
        try:
            self._check_origin()
            path = urlsplit(self.path).path
            if method == "GET" and path == "/health":
                return self._json(200, {"status": "ok", "service": "nisar-local-api", "scientific": False})
            if method == "GET" and path == "/":
                return self._send(200, HELP_HTML.encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path == "/api/search" and method in {"GET", "POST"}:
                if method == "GET":
                    values = self._query()
                else:
                    values = self._body()
                return self._json(200, self.server.client.search(**search_parameters(values)))
            if method == "POST" and path == "/api/subset-plan":
                from .harmony import prepare_subset_plan
                values = self._body()
                if not isinstance(values, dict) or set(values) - {"product_id", "bbox", "variables"}:
                    raise ApiError("invalid_request", "Subset parameters must be product_id, bbox, and optional variables.")
                product_id = values.get("product_id")
                if not isinstance(product_id, str) or not product_id or len(product_id) > 512:
                    raise ApiError("invalid_product_id", "A product_id is required.")
                bbox = search_parameters({"bbox": values.get("bbox")})["bbox"]
                variables = values.get("variables")
                if variables is not None and (not isinstance(variables, list) or not all(isinstance(v, str) and v for v in variables)):
                    raise ApiError("invalid_variables", "variables must be a list of nonempty variable names.")
                item = self.server.client.get_product(product_id)
                try:
                    plan = prepare_subset_plan(self.server.client.transport, item, bbox, variables=variables)
                except ValueError as error:
                    raise ApiError("invalid_subset_plan", str(error)) from None
                return self._json(200, plan)
            if method == "GET" and path == "/api/bundle":
                return self._send(200, build_bundle(self.server.client, self._query()), "application/zip", "nisar-browse-bundle.zip")
            match = re.fullmatch(r"/api/products/([^/]+)(?:/previews/([0-9]+))?", path)
            if method == "GET" and match:
                product_id = unquote(match.group(1))
                if not product_id or len(product_id) > 512 or any(c in product_id for c in "/\\\x00\r\n"):
                    raise ApiError("invalid_product_id", "The product identifier is invalid.")
                if match.group(2) is None:
                    return self._json(200, self.server.client.get_product(product_id))
                if len(match.group(2)) > 3:
                    raise ApiError("invalid_preview_index", "The preview index is out of range.")
                index = int(match.group(2))
                if index > 100:
                    raise ApiError("invalid_preview_index", "The preview index is out of range.")
                preview = self.server.client.preview(product_id, index=index)
                return self._send(200, preview["content"], preview["media_type"])
            raise ApiError("not_found", "No API endpoint matches this request.", status=404)
        except ApiError as error:
            self._json(error.status, {"error": {"code": error.code, "message": error.message, "status": error.status, "details": error.details}})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500, {"error": {"code": "internal_error", "message": "The local API could not complete this request.", "status": 500, "details": None}})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        try:
            self._check_origin()
            self.send_response(HTTPStatus.NO_CONTENT)
            origin = self.headers.get("Origin")
            if origin in self.server.allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except ApiError as error:
            self._json(error.status, {"error": {"code": error.code, "message": error.message, "status": error.status, "details": error.details}})


def create_server(client, host="127.0.0.1", port=4174, public_origin=None):
    if host not in {"127.0.0.1", "localhost"}:
        raise ApiError("invalid_host", "This API binds only to 127.0.0.1 or localhost.")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ApiError("invalid_port", "The port must be an integer from 0 to 65535.")
    configured_origin = os.environ.get("NISAR_PUBLIC_ORIGIN") if public_origin is None else public_origin
    return NisarHTTPServer((host, port), client, public_origin=validate_public_origin(configured_origin))
