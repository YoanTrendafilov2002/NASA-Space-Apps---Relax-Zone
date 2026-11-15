"""Screen real NISAR preview files around a selected acquisition."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import isfinite
import re
from threading import BoundedSemaphore

from explorer_catalogue import POLARIZATIONS, nisar_observations
from nisar_tool.client import MAX_PREVIEW_BYTES, MAX_RESULTS
from nisar_tool.errors import ApiError
from preview_image_analysis import compare_previews, describe_preview


DEFAULT_DAYS = 24
MAX_DAYS = 90
FRAMES_PER_SIDE = 3
BROWSE_PATH = re.compile(r"/api/products/(NISAR_[A-Za-z0-9_-]{6,290})/previews/([0-9]{1,3})\Z")


def acquired_day(row):
    try:
        return date.fromisoformat(row["acquired_at"][:10])
    except (KeyError, TypeError, ValueError):
        return None


def acquisition_geometry(row):
    try:
        track, frame = int(str(row.get("track") or row["orbit"])), int(str(row["frame"]))
    except (KeyError, TypeError, ValueError):
        return None
    direction = str(row.get("direction")).strip().lower()
    direction = {"a": "ascending", "d": "descending"}.get(direction, direction)
    wavelength, frequency = row.get("wavelength"), row.get("frequency")
    if (not 1 <= track <= 173 or not 1 <= frame <= 176 or direction not in ("ascending", "descending")
            or wavelength not in ("L", "S") or frequency not in ("A", "B")):
        return None
    return track, frame, direction, wavelength, frequency


def search_bounds(row, site):
    if site and site.get("bbox"):
        return site["bbox"]
    geometry = row.get("source_geometry") or row.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        coordinates = [coordinates]
    elif geometry.get("type") != "MultiPolygon":
        return None
    try:
        points = [(x, y) for polygon in coordinates for ring in polygon for x, y, *_ in ring
                  if isfinite(x) and isfinite(y)]
    except (TypeError, ValueError):
        return None
    if not points:
        return None
    xs, ys = zip(*points)
    west, south, east, north = min(xs), min(ys), max(xs), max(ys)
    # At the antimeridian, leave discovery to a prepared site rather than
    # sending a nearly global bounding box.
    if not -180 <= west < east <= 180 or not -90 <= south < north <= 90 or east - west > 30:
        return None
    return [west, south, east, north]


def prepared_style(channel):
    bounds, stretch = channel.get("image_bounds"), channel.get("display_range_db")
    if bounds and stretch:
        return bounds, stretch, channel.get("display_epsg")
    return None


def summarize_frames(target, frames):
    if not target["preview_url"]:
        return "The selected image is unavailable. You can still browse any nearby captures that loaded."
    comparisons = [f["appearance"]["status"] for f in frames if f["appearance"]]
    if "different" in comparisons:
        return ("At least one nearby image looks different overall. Image processing can shift brightness too, "
                "so use the images to pick dates for a closer check.")
    if "similar" in comparisons:
        return ("The images I could compare have similar overall brightness. Zoom in to check for smaller changes "
                "around the coast, rivers or wetlands.")
    if comparisons:
        return ("The images cover or show the area differently. Use the slider to inspect each capture. "
                "The app needs a matching pair to compare brightness.")
    return "The selected image is the only one available to compare in this window. Try more days to find another capture."


class ExplorerPreviewAnalysis:
    def __init__(self, catalogue, raster_routes):
        self.catalogue = catalogue
        self.raster_routes = raster_routes
        self.slots = BoundedSemaphore(2)

    def run(self, params):
        if not isinstance(params, dict) or set(params) - {"observation_id", "polarization", "days"}:
            raise ApiError("invalid_preview_request", "Choose one acquisition, one polarization, and a day window.")
        ident, pol, days = params.get("observation_id"), params.get("polarization"), params.get("days", DEFAULT_DAYS)
        if not isinstance(ident, str) or not 1 <= len(ident) <= 320 or pol not in POLARIZATIONS:
            raise ApiError("invalid_preview_request", "Choose an acquisition and one of HH, HV, VV or VH.")
        if type(days) is not int or not 1 <= days <= MAX_DAYS:
            raise ApiError("invalid_preview_window", f"Choose a whole number of days from 1 to {MAX_DAYS}.")
        rows = self.catalogue.known([ident])
        if not rows:
            raise ApiError("unknown_observation", "Select an acquisition again, or rerun the catalogue search.", 404)
        anchor = rows[0]
        day = acquired_day(anchor)
        if day is None or anchor["product"] != "NISAR GCOV" or acquisition_geometry(anchor) is None:
            raise ApiError("missing_preview_geometry", "Choose a NISAR GCOV acquisition with a known track, frame and pass direction.")
        if anchor["channels"].get(pol, {}).get("status") == "unavailable":
            raise ApiError("unavailable_polarization", f"The selected acquisition did not collect {pol}.")
        if not self.slots.acquire(blocking=False):
            raise ApiError("preview_analysis_busy", "Preview screening is busy. Try again in a moment.", 429)
        try:
            return self._run(anchor, day, pol, days)
        finally:
            self.slots.release()

    def _run(self, anchor, day, pol, days):
        geometry = acquisition_geometry(anchor)
        site_id = anchor.get("site_id")
        bbox = search_bounds(anchor, self.catalogue.sites.get(site_id))
        start, end = day - timedelta(days=days), day + timedelta(days=days)
        messages = []
        partial = False
        live = {}
        if bbox is not None:
            try:
                result = self.catalogue.client.search(
                    bbox=bbox, start=str(start), end=str(end), product="GCOV",
                    limit=MAX_RESULTS, previews="links", track=geometry[0], frame=geometry[1])
                for item in result["items"]:
                    for row in nisar_observations(item):
                        if acquisition_geometry(row) == geometry:
                            live[row["product_id"]] = row
                partial = result["count"] >= MAX_RESULTS
                if partial:
                    messages.append("ASF reached its search result limit. Narrow the window if a nearer date is missing.")
            except ApiError:
                partial = True
                messages.append("ASF catalogue search was unavailable. The timeline shows saved acquisitions where available.")
        else:
            partial = True
            messages.append("This acquisition has no usable search footprint. The timeline shows saved acquisitions where available.")

        # Saved observations take precedence for this study area. Prefer a
        # known study-area crop to a separate copy of the same provider scene.
        by_product = dict(live)
        for row in self.catalogue.seed.values():
            if site_id and row.get("site_id") == site_id and row["product"] == anchor["product"] and acquisition_geometry(row) == geometry:
                by_product[row["product_id"]] = row
        by_product[anchor["product_id"]] = anchor
        before, after = {}, {}
        def has_preview(row):
            channel = row["channels"].get(pol, {})
            return bool(channel.get("image_url") or channel.get("preview_url"))

        for row in sorted(by_product.values(), key=lambda row: (not has_preview(row), row["id"])):
            candidate_day = acquired_day(row)
            if candidate_day is None or not start <= candidate_day <= end or candidate_day == day:
                continue
            offset = (candidate_day - day).days
            if row["channels"].get(pol, {}).get("status") != "unavailable":
                (before if offset < 0 else after).setdefault(offset, row)
        if len(before) > FRAMES_PER_SIDE or len(after) > FRAMES_PER_SIDE:
            messages.append(f"Showing up to {FRAMES_PER_SIDE} nearest acquisitions on each side of the selected date.")
        for entries, side in ((before, "before"), (after, "after")):
            if not entries:
                messages.append(f"No matching {pol} acquisition found {side} the selected date in this window.")
        if end > datetime.now(timezone.utc).date():
            messages.append("The later window extends past today. The timeline can only show published acquisitions.")

        target_frame, target_features, target_style = self._load_frame(anchor, live.get(anchor["product_id"]), pol, 0)
        frames = [target_frame]
        offsets = sorted(before, reverse=True)[:FRAMES_PER_SIDE] + sorted(after)[:FRAMES_PER_SIDE]
        for offset in offsets:
            row = (before if offset < 0 else after)[offset]
            frame, features, style = self._load_frame(row, live.get(row["product_id"]), pol, offset)
            if target_features is not None and features is not None and target_style and style == target_style:
                frame["appearance"] = compare_previews(target_features, features)
            elif target_frame["preview_url"] and frame["preview_url"]:
                frame["appearance"] = {
                    "status": "not_comparable",
                    "summary": "The available previews differ in coverage, crop, or rendering. Inspect the images separately.",
                }
            frames.append(frame)
        if not target_frame["preview_url"]:
            messages.append("The selected date has no readable preview in this polarization. Nearby previews provide context only.")
        missing = sum(frame["preview_url"] is None for frame in frames)
        if missing:
            messages.append(f"{missing} of {len(frames)} acquisitions have no readable {pol} preview.")
        return {"scope": "visual_only", "days": days, "polarization": pol, "partial": partial,
                "summary": summarize_frames(target_frame, frames), "messages": messages,
                "frames": sorted(frames, key=lambda f: f["offset_days"])}

    def _load_frame(self, row, live, pol, offset):
        channel = row["channels"].get(pol, {})
        frame = {"observation_id": row["id"], "acquired_at": row["acquired_at"], "offset_days": offset,
                 "preview_url": None, "preview_kind": None, "source_url": row.get("source_url"), "appearance": None}
        crop = channel.get("image_url")
        asset = self.raster_routes.get(crop)
        if asset and asset[1] in ("image/png", "image/webp"):
            try:
                with asset[0].open("rb") as stream:
                    features = describe_preview(stream.read(MAX_PREVIEW_BYTES + 1))
                if features is not None:
                    frame.update(preview_url=crop, preview_kind="prepared_crop")
                    style = prepared_style(channel)
                    return frame, features, ("prepared_crop", style) if style else None
            except OSError:
                pass

        browse = channel.get("preview_url") or (live or {}).get("channels", {}).get(pol, {}).get("preview_url")
        match = BROWSE_PATH.fullmatch(browse) if isinstance(browse, str) else None
        if match and match[1] == row["product_id"]:
            try:
                preview = self.catalogue.client.preview(match[1], index=int(match[2]))
                features = describe_preview(preview["content"])
                if features is not None:
                    frame.update(preview_url=browse, preview_kind="provider_frame")
                    parts = row["product_id"].split("_")
                    # Full coverage plus the same bandwidth, acquisition mode
                    # and release allow a conservative whole-frame tone check.
                    style = ("provider_frame", parts[8], parts[9], parts[13]) if len(parts) == 18 and parts[15] == "F" else None
                    return frame, features, style
            except ApiError:
                pass
        return frame, None, None
