"""Bounded public catalogue adapters for the shared selection workspace."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, timedelta
import json
import re
from threading import Lock
from time import monotonic
from urllib.parse import quote

from nisar_tool.errors import ApiError
from nisar_tool.server import search_parameters


POLARIZATIONS = ("VV", "VH", "HH", "HV")
PROVIDER_LISTED_CHANNELS = frozenset({"advertised", "preview", "catalogue_only"})
MISSIONS = frozenset({"all", "nisar"})
NISAR_PRODUCTS = frozenset({"GCOV", "GUNW", "GOFF", "SME2", "GSLC", "RSLC"})
SITE_MANIFESTS = ("non-sentinel-nisar.json", "nisar-burgas.json", "nisar-uae-coastal-review.json", "nisar-everglades.json", "nisar-maranon.json", "nisar-holdouts.json", "nisar-wetland-holdout.json", "nisar-hormuz-sep10-reference.json", "nisar-imported.json")
PROVIDER_EVIDENCE = (
    {"id": "asf-nisar-gcov", "title": "ASF · How to read NISAR radar brightness", "kind": "Product guide",
     "url": "https://nisar-docs.asf.alaska.edu/gcov/",
     "notes": "ASF documents calibrated gamma-zero backscatter, polarization and the product grid."},
)


def iso_date(value, label):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ApiError("invalid_date", f"Use YYYY-MM-DD for {label}.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError("invalid_date", f"Enter a valid date for {label}.") from None


def channel_status(polarizations, channel):
    if not polarizations:
        return "unknown"
    return "advertised" if channel in polarizations else "unavailable"


def nisar_observations(item):
    props = item["provider_metadata"]
    sensor = props.get("sensor") or "NISAR"
    wavelength = {"L-SAR": "L", "S-SAR": "S"}.get(sensor)
    ident = item["id"]
    frequency_polarizations = [("A", props.get("mainBandPolarization")), ("B", props.get("sideBandPolarization"))]
    for freq, polarizations in frequency_polarizations:
        polarizations = polarizations if isinstance(polarizations, list) else []
        if freq == "B" and not polarizations:
            continue
        channels = {}
        for channel in POLARIZATIONS:
            status = channel_status(polarizations, channel)
            entry = {"status": status}
            if item["product_type"] == "GCOV" and status == "advertised":
                term = channel * 2
                entry.update(term=term, units="gamma0 power", dataset_path=f"/science/LSAR/GCOV/grids/frequency{freq}/{term}")
                preview = next((p for p in item["previews"]
                                if p["label"].upper().endswith(f"_NATIVE_{freq}_{term}.PNG")), None)
                if preview:
                    entry.update(status="preview", preview_url=preview["url"])
            channels[channel] = entry
        yield {"id": f"{ident}__{freq}", "product_id": ident, "mission": "nisar", "provider": "NASA ASF DAAC",
               "product": f"NISAR {item['product_type']}", "sensor": sensor, "wavelength": wavelength,
               "acquired_at": item["acquisition_start"], "acquisition_id": ident,
               "orbit": props.get("pathNumber"), "frame": props.get("frameNumber"),
               "direction": props.get("flightDirection"), "mode": props.get("beamModeType"),
               "frequency": freq, "geometry": item["geometry"], "channels": channels,
               "preview_url": item["previews"][0]["url"] if item["previews"] else None,
               "science_requires_earthdata": True, "evidence_id": "asf-nisar-gcov",
               "source_url": "https://search.asf.alaska.edu/#/?granule=" + quote(ident, safe=""),
               "processing_version": item.get("processing_version"), "acquisition_end": item.get("acquisition_end")}


class ExplorerCatalogue:
    def __init__(self, client, path, additional_paths=()):
        self.client = client
        self.data = json.loads(path.read_text(encoding="utf-8-sig"))
        for extra_path in additional_paths:
            if extra_path.is_file():
                extra = json.loads(extra_path.read_text(encoding="utf-8-sig"))
                for field in ("sites", "observations", "evidence"):
                    ids = {row["id"] for row in self.data[field]}
                    self.data[field].extend(row for row in extra[field] if row["id"] not in ids)
        for row in self.data["observations"]:
            if not row["product"].startswith("NISAR ") or not row["product_id"].startswith("NISAR_"):
                raise ValueError(f"Expected a NISAR observation: {row['id']}")
        existing_evidence = {row["id"] for row in self.data["evidence"]}
        self.data["evidence"].extend(row for row in PROVIDER_EVIDENCE if row["id"] not in existing_evidence)
        self.sites = {s["id"]: s for s in self.data["sites"]}
        self.seed = {o["id"]: o for o in self.data["observations"]}
        self.cache = OrderedDict()
        self.cache_lock = Lock()

    def _local_matches(self, bbox, start, end, product):
        west, south, east, north = bbox
        result = []
        for obs in self.seed.values():
            acquired = obs["acquired_at"][:10]
            if not str(start) <= acquired <= str(end) or obs["product"] != f"NISAR {product}":
                continue
            for channel in obs["channels"].values():
                bounds = channel.get("image_bounds")
                if not bounds:
                    continue
                (ys, xw), (yn, xe) = bounds
                if xw < east and xe > west and ys < north and yn > south:
                    result.append(obs)
                    break
        return result

    def _remember(self, observations):
        with self.cache_lock:
            for obs in observations:
                self.cache[obs["id"]] = (monotonic(), obs)
                self.cache.move_to_end(obs["id"])
            while len(self.cache) > 500:
                self.cache.popitem(last=False)

    def known(self, ids):
        with self.cache_lock:
            return [self.seed.get(ident) or self.cache[ident][1] for ident in ids
                    if ident in self.seed or (ident in self.cache and monotonic() - self.cache[ident][0] < 1800)]

    def search(self, values):
        if not isinstance(values, dict) or set(values) - {"bbox", "start", "end", "mission", "product"}:
            raise ApiError("invalid_request", "Supply an area, dates and optionally a mission and product.")
        mission = values.get("mission") or "all"
        product = values.get("product") or "GCOV"
        if not isinstance(mission, str) or mission not in MISSIONS:
            raise ApiError("invalid_mission", "This explorer searches NISAR.")
        if not isinstance(product, str) or product not in NISAR_PRODUCTS | {"all"}:
            raise ApiError("invalid_product", "Select a supported NISAR product.")
        product = "GCOV" if product == "all" else product
        bbox = search_parameters({"bbox": values.get("bbox")})["bbox"]
        if bbox[2] - bbox[0] > 12 or bbox[3] - bbox[1] > 12:
            raise ApiError("area_too_large", "Narrow the search area to at most 12 degrees in each direction.")
        end = iso_date(values.get("end"), "end") or date.today()
        start = iso_date(values.get("start"), "start") or end - timedelta(days=89)
        if not 0 <= (end - start).days <= 366:
            raise ApiError("invalid_date_range", "Choose an ordered interval of no more than 367 calendar days.")
        rows = self._local_matches(bbox, start, end, product)
        present = {(row["product_id"], row.get("frequency")) for row in rows}
        errors = []
        try:
            result = self.client.search(bbox=bbox, start=str(start), end=str(end), product=product, limit=10, previews="links")
            for item in result["items"]:
                rows.extend(obs for obs in nisar_observations(item) if (obs["product_id"], obs["frequency"]) not in present)
            partial = result["count"] >= 10
        except ApiError as exc:
            if not rows:
                raise
            errors.append({"provider": "NASA ASF DAAC", "code": exc.code, "message": exc.message})
            partial = True
        rows.sort(key=lambda item: (item.get("acquired_at") or "", item["id"]), reverse=True)
        self._remember(rows)
        return {"observations": rows, "partial": partial, "errors": errors,
                "query": {"bbox": bbox, "start": str(start), "end": str(end), "mission": "nisar", "product": product}}
