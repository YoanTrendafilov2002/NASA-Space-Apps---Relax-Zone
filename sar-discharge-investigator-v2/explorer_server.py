"""Serve the Relax Zone UI and a bounded public catalogue on one origin."""
from __future__ import annotations

import argparse
from contextlib import suppress
from hashlib import file_digest
import ipaddress
import json
import logging
from math import isfinite
from pathlib import Path
import re
import socket
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from explorer_analysis import ExplorerAnalysis
from explorer_atbd import EcosystemsKnowledge
from explorer_assistant import ExplorerAssistant
from explorer_catalogue import ExplorerCatalogue, SITE_MANIFESTS
from explorer_glossary import ExplorerGlossary
from explorer_preview_analysis import ExplorerPreviewAnalysis
from explorer_settings import MissionReferences, UseCaseSettings
from explorer_synopsis import dataset_synopsis, screen_profiles
from matrix_answers import MatrixAnswers, SCIENCE_SOURCES
from matrix_intent import MatrixIntent
from nisar_tool.errors import ApiError
from nisar_tool.runtime import create_client
from nisar_tool.server import HELP_HTML, NisarHandler, NisarHTTPServer, validate_public_origin
from nisar_tool.settings import Settings


APP = Path(__file__).resolve().parent
OUTPUTS = APP.parent.parent
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/explorer.js": ("explorer.js", "text/javascript; charset=utf-8"),
    "/explorer.css": ("explorer.css", "text/css; charset=utf-8"),
    "/explorer-manual.js": ("explorer-manual.js", "text/javascript; charset=utf-8"),
    "/data/basemap-land.geojson": ("data/basemap-land.geojson", "application/geo+json"),
    "/data/basemap-lakes.geojson": ("data/basemap-lakes.geojson", "application/geo+json"),
    "/science-review": ("evidence.html", "text/html; charset=utf-8"),
    "/evidence.css": ("evidence.css", "text/css; charset=utf-8"),
    "/science-review-notes": ("NISAR_DATA_REVIEW.md", "text/plain; charset=utf-8"),
    "/science-review-wetlands": ("results/recognition/wetlands/v2-validation.json", "application/json"),
    "/science-review-alenquer": ("results/recognition/wetlands/v2-alenquer.json", "application/json"),
    "/vendor/leaflet.js": ("vendor/leaflet.js", "text/javascript; charset=utf-8"),
    **{f"/vendor/{name}.css": (f"vendor/{name}.css", "text/css; charset=utf-8") for name in ("leaflet", "fonts")},
    **{f"/vendor/{name}.woff2": (f"vendor/{name}.woff2", "font/woff2") for name in ("montagu-slab", "source-sans-3")},
    **{f"/vendor/images/{name}.png": (f"vendor/images/{name}.png", "image/png") for name in (
        "layers", "layers-2x", "marker-icon", "marker-icon-2x", "marker-shadow")},
}
ASSET_PATTERN = re.compile(r"/(sar|science)/([a-z0-9][a-z0-9_-]{0,119}\.(?:png|webp|tif))\Z")
ASSET_TYPES = {".png": "image/png", ".webp": "image/webp", ".tif": "image/tiff"}


def read_evidence_manifest(catalogue, path, records_key):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or not isinstance(manifest.get(records_key), list) or not isinstance(manifest.get("evidence"), list)):
        raise ValueError(f"Invalid evidence manifest: {path.name}.")
    sources = {item["id"]: item for item in catalogue.data["evidence"]}
    for item in manifest["evidence"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError(f"Missing evidence ID in {path.name}.")
        sources.setdefault(item["id"], item)
    return manifest[records_key], sources


def load_recognition_reviews(catalogue, path):
    if not path.is_file():
        return
    layers, sources = read_evidence_manifest(catalogue, path, "layers")
    previous = catalogue.data.get("recognition_layers", [])
    review_ids = {record["id"] for record in previous}
    selections = {(record["site_id"], record["observation_id"], record["polarization"]) for record in previous}
    for layer in layers:
        if (not isinstance(layer, dict) or any(not isinstance(layer.get(key), str) or not layer[key].strip()
                for key in ("id", "site_id", "observation_id", "polarization", "title", "summary"))):
            raise ValueError("Incomplete recognition review record.")
        selection = layer["site_id"], layer["observation_id"], layer["polarization"]
        site_id, observation_id, polarization = selection
        observation = catalogue.seed.get(observation_id, {})
        parent = observation.get("channels", {}).get(polarization, {})
        refs = layer.get("evidence_ids")
        outlines = layer.get("outlines")
        vector_review = (isinstance(outlines, dict) and outlines.get("type") == "FeatureCollection"
                         and isinstance(outlines.get("features"), list) and bool(outlines["features"]))
        if (not vector_review and not layer.get("image_url")) or (outlines is not None and not vector_review):
            raise ValueError(f"Missing recognition review display for {layer['id']}.")
        if (layer["id"] in review_ids or selection in selections or observation.get("site_id") != site_id
                or not parent.get("image_url") or not parent.get("science_url")
                or not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in sources for ref in refs)):
            raise ValueError(f"Unmatched recognition review or evidence for {layer['id']}.")
        review_ids.add(layer["id"])
        selections.add(selection)
    catalogue.data["evidence"] = list(sources.values())
    catalogue.data["recognition_layers"] = [*previous, *layers]


def load_time_series(catalogue, path):
    if not path.is_file():
        return
    records, sources = read_evidence_manifest(catalogue, path, "series")
    series_ids = set()
    selections = set()
    for series in records:
        if (not isinstance(series, dict) or series.get("units") != "dB"
                or any(not isinstance(series.get(key), str) or not series[key].strip()
                       for key in ("id", "site_id", "polarization", "title", "summary", "statistic"))):
            raise ValueError("Incomplete radar time series record.")
        site_id, polarization = series["site_id"], series["polarization"]
        points = series.get("points")
        refs = series.get("evidence_ids")
        if (series["id"] in series_ids or (site_id, polarization) in selections
                or not isinstance(points, list) or len(points) < 2
                or not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in sources for ref in refs)):
            raise ValueError(f"Incomplete time series or evidence for {series['id']}.")
        series_ids.add(series["id"])
        selections.add((site_id, polarization))
        dates = set()
        counts = set()
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("observation_id"), str):
                raise ValueError(f"Invalid observation in {series['id']}.")
            observation = catalogue.seed.get(point["observation_id"], {})
            parent = observation.get("channels", {}).get(polarization, {})
            acquired_at, value, count = point.get("acquired_at"), point.get("value"), point.get("valid_pixels")
            if (observation.get("site_id") != site_id or not parent.get("science_url")
                    or not isinstance(acquired_at, str) or acquired_at != observation.get("acquired_at") or acquired_at in dates
                    or type(value) not in (int, float) or not isfinite(value) or type(count) is not int or count <= 0):
                raise ValueError(f"Mismatched time series sample in {series['id']}.")
            dates.add(acquired_at)
            counts.add(count)
        if len(counts) != 1:
            raise ValueError(f"Time series samples use different support sizes in {series['id']}.")
    catalogue.data["evidence"] = list(sources.values())
    catalogue.data["time_series"] = records


def raster_routes(catalogue):
    """Expose only prepared raster assets explicitly assigned in the catalogue."""
    routes = {}

    def register(asset, key, identifier, *, verify_receipt=False):
        url = asset.get(key)
        if not url:
            return
        match = ASSET_PATTERN.fullmatch(url) if isinstance(url, str) else None
        if not match:
            raise ValueError(f"Invalid raster URL for {identifier}")
        folder, name = match.groups()
        is_science = key == "science_url"
        root = (APP / "data" / folder).resolve()
        file = (root / name).resolve()
        if ((folder == "science") != is_science or not root.is_relative_to(APP) or not file.is_relative_to(root)
                or not file.is_file() or not 0 < file.stat().st_size <= 50_000_000
                or (file.suffix == ".tif") != is_science or url in routes):
            raise ValueError(f"Missing or invalid prepared asset for {identifier}: {url}")
        if verify_receipt:
            prefix = key.removesuffix("_url")
            if asset.get(f"{prefix}_size_bytes") != file.stat().st_size:
                raise ValueError(f"Recognition review size mismatch for {identifier}: {url}")
            with file.open("rb") as stream:
                if asset.get(f"{prefix}_sha256") != file_digest(stream, "sha256").hexdigest():
                    raise ValueError(f"Recognition review SHA-256 mismatch for {identifier}: {url}")
        routes[url] = file, ASSET_TYPES[file.suffix], name if is_science else None

    for observation in catalogue.seed.values():
        for channel in observation["channels"].values():
            register(channel, "image_url", observation["id"])
            register(channel, "science_url", observation["id"])
    for layer in catalogue.data.get("change_layers", []):
        register(layer, "image_url", layer["id"])
        register(layer, "science_url", layer["id"])
    for layer in catalogue.data.get("recognition_layers", []):
        register(layer, "image_url", layer["id"], verify_receipt=True)
        register(layer, "science_url", layer["id"], verify_receipt=True)
    return routes


class ExplorerHandler(NisarHandler):
    server_version = "RelaxZoneExplorer/1"

    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def _visitor(self):
        if self.server.public_origin:
            header = self.headers.get("CF-Connecting-IP", "")
            with suppress(ValueError):
                return str(ipaddress.ip_address(header))
        return self.client_address[0]

    def _handle(self, method):
        try:
            self._check_origin()
            path = urlsplit(self.path).path
            if method in ("GET", "HEAD") and path.startswith(("/sar/nisar-analysis-", "/science/nisar-analysis-")):
                asset = self.server.analysis.asset(path)
                if asset:
                    content, media_type, download = asset
                    if method == "GET":
                        return self._send(200, content, media_type, filename=download)
                    self.send_response(200)
                    self.send_header("Content-Type", media_type)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-store")
                    if download:
                        self.send_header("Content-Disposition", f'attachment; filename="{download}"')
                    self.end_headers()
                    return None
                if method == "HEAD":
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                raise ApiError("analysis_file_unavailable", "This analysis file is unavailable. Run the analysis again to regenerate it.", 404)
            if method in ("GET", "HEAD") and path in self.server.raster_routes:
                file, media_type, download = self.server.raster_routes[path]
                if method == "HEAD":
                    self.send_response(200)
                    self.send_header("Content-Type", media_type)
                    self.send_header("Content-Length", str(file.stat().st_size))
                    if download:
                        self.send_header("Content-Disposition", f'attachment; filename="{download}"')
                    self.end_headers()
                    return None
                return self._send(200, file.read_bytes(), media_type, filename=download)
            if method == "GET" and path in STATIC:
                name, content_type = STATIC[path]
                file = (APP / name).resolve()
                if not file.is_relative_to(APP) or not file.is_file():
                    raise ApiError("not_found", "This app asset is unavailable.", 404)
                return self._send(200, file.read_bytes(), content_type)
            if method == "GET" and path == "/nisar":
                return self._send(200, HELP_HTML.encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and path == "/health":
                return self._json(200, {"status": "ok", "service": "relax-zone-explorer",
                                        "sites": len(self.server.catalogue.sites), "recorded_observations": len(self.server.catalogue.seed)})
            if method == "GET" and path == "/api/explorer/bootstrap":
                return self._json(200, self.server.catalogue.data)
            if method == "GET" and path == "/api/explorer/experiments":
                return self._json(200, self.server.experiments)
            if method == "POST" and path == "/api/explorer/analyze":
                return self._json(200, self.server.analysis.run(self._body(), self._visitor()))
            if method == "POST" and path == "/api/explorer/preview-analysis":
                return self._json(200, self.server.preview_analysis.run(self._body()))
            if method == "POST" and path == "/api/explorer/search":
                if not self.server.discovery_slots.acquire(blocking=False):
                    raise ApiError("catalogue_busy", "The catalogue is busy. Try again in a moment.", 429)
                try:
                    return self._json(200, self.server.catalogue.search(self._body()))
                finally:
                    self.server.discovery_slots.release()
            if method == "POST" and path == "/api/assistant":
                body = self._body()
                return self._json(200, self.server.assistant.respond(body, self._visitor()))
            return super()._handle(method)
        except ApiError as error:
            return self._json(error.status, {"error": {"code": error.code, "message": error.message, "details": error.details}})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return None
        except Exception as error:
            logging.getLogger(__name__).error("Explorer request failed (%s)", type(error).__name__)
            return self._json(500, {"error": {"code": "internal_error", "message": "The explorer could not complete this request."}})

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path in self.server.raster_routes or path.startswith(("/sar/nisar-analysis-", "/science/nisar-analysis-")):
            return self._handle("HEAD")
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


class ExplorerServer(NisarHTTPServer):
    def __init__(self, port, public_origin=None, *, client=None, catalogue_path=None, inference=None, without_ollama=False):
        settings = Settings()
        # In this workspace the prior client and this repo use one transfer ledger.
        prior = OUTPUTS / "nisar_api" / "state"
        if APP.parent.name == "relax-zone" and prior.is_dir():
            settings.state_dir = prior
        baseline = OUTPUTS / "sofia_sar" / "evidence" / "transfer_ledger.jsonl"
        if APP.parent.name == "relax-zone" and baseline.is_file():
            settings.baseline_ledger = baseline
        client = client or create_client(settings)
        primary, *additional = (APP / "data" / name for name in SITE_MANIFESTS)
        self.catalogue = ExplorerCatalogue(client, catalogue_path or primary, additional_paths=() if catalogue_path else additional)
        options = {"without_ollama": without_ollama}
        self.experiments = {"experiments": []}
        if inference:
            options["inference"] = inference
        if catalogue_path is None:
            context = json.loads((APP / "data" / "nisar-reference-context.json").read_text(encoding="utf-8"))
            self.experiments = json.loads((APP / "data" / "nisar-experiments.json").read_text(encoding="utf-8"))
            evidence = {item["id"]: item for item in (*self.catalogue.data["evidence"], *SCIENCE_SOURCES,
                                                      *context["evidence"], *self.experiments["evidence"])}
            self.catalogue.data["evidence"] = list(evidence.values())
            self.catalogue.data["evidence"].append({
                "id": "wetland-v2-validation", "title": "Wetland screen comparison on real NISAR",
                "kind": "Research validation", "url": "/science-review-wetlands",
                "notes": "Curuai replay, Hasik unseen-site test, and the UAE quality abstention. "
                         "Open-water evidence and flooded-vegetation transfer have separate scopes.",
            })
            layers = json.loads((APP / "data" / "nisar-change-layers.json").read_text(encoding="utf-8"))["layers"]
            self.catalogue.data["change_layers"] = layers
            load_recognition_reviews(self.catalogue, APP / "data" / "nisar-detections.json")
            load_recognition_reviews(self.catalogue, APP / "data" / "nisar-wetland-context.json")
            load_time_series(self.catalogue, APP / "data" / "nisar-time-series.json")
            glossary_path = APP / "data" / "glossary.json"
            if glossary_path.is_file():
                glossary = ExplorerGlossary(glossary_path, getattr(client, "transport", None))
                self.catalogue.data["glossary"] = glossary.data
                self.catalogue.data["evidence"].extend({**source, "kind": "Glossary reference"}
                                                       for source in glossary.sources.values())
                options["glossary"] = glossary
            ecosystems = EcosystemsKnowledge(APP / "data" / "nisar-ecosystems-atbd.json")
            self.catalogue.data["evidence"].extend(ecosystems.sources())
            options["ecosystems"] = ecosystems
            mission = MissionReferences(APP / "data" / "nisar-mission-atbd.json")
            settings_guide = UseCaseSettings(APP / "data" / "nisar-use-case-settings.json")
            self.catalogue.data["evidence"].extend([*mission.sources(), *settings_guide.sources])
            profiles = settings_guide.public_profiles()
            available_sources = {source["id"] for source in self.catalogue.data["evidence"]}
            if any(set(profile["evidence_ids"]) - available_sources for profile in profiles):
                raise ValueError("A use-case settings profile references an unregistered source.")
            self.catalogue.data["use_case_settings"] = profiles
            options.update(mission_references=mission, settings_guide=settings_guide)
            options["answers"] = MatrixAnswers(self.catalogue, APP / "data" / "nisar-measurements.json", change_layers=layers,
                                               water_levels=context["water_levels"], experiments=self.experiments["experiments"],
                                               time_series=self.catalogue.data.get("time_series", ()))
            options["intent_model"] = MatrixIntent(APP / "data" / "matrix-assistant-model.json")
        self.raster_routes = raster_routes(self.catalogue)
        self.analysis = ExplorerAnalysis(APP, self.catalogue)
        self.preview_analysis = ExplorerPreviewAnalysis(self.catalogue, self.raster_routes)
        self.catalogue.data["analysis_available"] = True
        self.catalogue.data["preview_analysis_available"] = True
        self.catalogue.data["analysis_model_observations"] = self.analysis.model_observations()
        self.catalogue.data["dataset_screen_profiles"] = screen_profiles(self.analysis.recognition.manifest["rows"])
        self.catalogue.data["dataset_synopses"] = {
            ident: {pol: dataset_synopsis(self.catalogue, row, pol) for pol in row["channels"]
                    if row["channels"][pol].get("status") != "unavailable"}
            for ident, row in self.catalogue.seed.items()
        }
        self.assistant = ExplorerAssistant(self.catalogue, **options)
        self.discovery_slots = BoundedSemaphore(3)
        super().__init__(("127.0.0.1", port), client, public_origin=validate_public_origin(public_origin))
        self.RequestHandlerClass = ExplorerHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4174)
    parser.add_argument("--public-origin", help="Exact HTTPS origin printed by the Cloudflare tunnel")
    parser.add_argument("--without-ollama", action="store_true", help="Run the trained matrix assistant and map commands without local Qwen")
    args = parser.parse_args()
    with ExplorerServer(args.port, args.public_origin, without_ollama=args.without_ollama) as server:
        print(f"Relax Zone at http://127.0.0.1:{server.server_port}/", flush=True)
        with suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
