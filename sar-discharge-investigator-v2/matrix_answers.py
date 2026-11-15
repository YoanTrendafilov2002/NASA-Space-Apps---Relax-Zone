"""Explain verified NISAR comparisons in the terms a map reader uses."""
from datetime import date
import json
import re

from explorer_catalogue import PROVIDER_LISTED_CHANNELS


SCIENCE_SOURCES = (
    {"id": "wetland-field-study", "title": "L-band study · Water under vegetation and open water",
     "url": "https://repository.library.noaa.gov/view/noaa/59167", "kind": "Peer-reviewed study",
     "notes": "The field study describes how open water and water under plants can have different radar signatures."},
    {"id": "oil-field-study", "title": "L-band oil response experiments · Surface roughness and wind",
     "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5856168/", "kind": "Peer-reviewed field experiment",
     "notes": "The study used controlled releases and field observations to assess when radar can distinguish surface films."},
    {"id": "drought-indicators", "title": "WMO and GWP · Using several indicators to assess drought",
     "url": "https://www.droughtmanagement.info/find/guidelines-tools/handbook-drought-indicators-and-indices/",
     "kind": "Scientific handbook", "notes": "Drought assessment depends on the type of drought, local conditions and comparison with historical conditions."},
    {"id": "nisar-polarimetry", "title": "NASA JPL · What HH, HV, VH and VV mean",
     "url": "https://science.nasa.gov/mission/nisar/polarimetry/", "kind": "Mission guide",
     "notes": "The first letter is the send direction and the second is the receive direction."},
    {"id": "asf-radar-geometry", "title": "ASF · Radar geometry and shadow",
     "url": "https://hyp3-docs.asf.alaska.edu/hyp3-docs/guides/introduction_to_sar/#geometric-distortions", "kind": "Technical guide",
     "notes": "In steep terrain the radar can see a slope from one orbit and miss it from another direction."},
    {"id": "emsa-slick-context", "title": "EMSA · Why slicks and calm water can look alike",
     "url": "https://emsa.europa.eu/earth-observation-products/how-does-sar-detection-work/items.html", "kind": "Operational guidance",
     "notes": "Wind ripples scatter radar back to the satellite. Oil and several natural processes can reduce that return."},
    {"id": "eugene-incident-record", "title": "NOAA · Eugene Island mystery sheens",
     "url": "https://incidentnews.noaa.gov/incident/11216", "kind": "Incident report",
     "notes": "NOAA reports recurring sheens and an investigation into their source."},
    {"id": "eloi-incident-record", "title": "NOAA · Eloi Bay wellhead incident",
     "url": "https://incidentnews.noaa.gov/incident/11235", "kind": "Incident report",
     "notes": "The incident report documents crude oil and sheen in the bay."},
    {"id": "atanasovsko-water-regime", "title": "Ramsar Site Information Sheet · Atanasovsko Lake",
     "url": "https://rsis.ramsar.org/RISapp/files/RISrep/BG292RIS_2001_en.pdf", "kind": "Habitat and management record",
     "notes": "The lagoon contains reedbeds and managed salt pans that depend on its water regime."},
    {"id": "qeshm-tidal-context", "title": "UNESCO · Hara mangroves and tidal wetlands",
     "url": "https://www.unesco.org/en/mab/hara", "kind": "Habitat record",
     "notes": "The protected area near Qeshm contains tidal marshes, mangroves and shallow coves."},
)
INTENTS = ("compare", "change", "time_series", "polarization", "coverage", "oil", "wetlands", "drought", "legend")
MEASURED = "validated"
OIL_MECHANISM = ("Oil can flatten tiny ripples made by wind. The smoother patch sends less radar back to NISAR and can look dark in HH. "
                 "Calm water or currents can produce a similar pattern.")
WETLAND_MECHANISM = ("Smooth open water often sends the radar signal away from NISAR and looks dark. "
                     "Flooded stems can look brighter when the signal bounces between the water and the stems and returns to the satellite.")
DROUGHT_MECHANISM = ("As water retreats, it exposes mud or plants that can reflect radar differently. "
                     "Compare the same season over several years and check rainfall and water levels. A normal seasonal retreat can look similar.")
SITE_CONTEXT = {
    "qeshm-nisar": ("Near Qeshm's tidal wetlands, check whether the images were taken at a similar tide.", ("qeshm-tidal-context",)),
    "gulf-eugene-nisar": ("NOAA tracked mystery sheens near Eugene Island. A timed reference helps check the shape, and identifying its source requires separate evidence.",
                          ("eugene-incident-record", "eugene-noaa-20260827", "nisar-observed-oil-screen")),
    "eloi-bay-nisar": ("NOAA documented oil in Eloi Bay. Compare the time of each reference with the radar pass because wind and currents can move a slick.",
                       ("eloi-incident-record", "eloi-noaa-20261012-a", "eloi-noaa-20261012-b")),
    "atanasovsko-nisar": ("Atanasovsko Lake has salt pans and a managed water regime. Check how the lake was managed before linking a change to drought.",
                          ("atanasovsko-water-regime", "atanasovsko-optical-20260713")),
}


def pretty_date(text):
    day = date.fromisoformat(text[:10])
    return f"{day.day} {day:%B %Y}"


def count_word(number, singular, plural=None):
    return singular if number == 1 else (plural or singular + "s")


class MatrixAnswers:
    def __init__(self, catalogue, path, change_layers=(), water_levels=(), experiments=(), time_series=()):
        self.catalogue = catalogue
        data = json.loads(path.read_text(encoding="utf-8"))
        self.observations = data["observations"]
        self.pairs = data["comparisons"]
        self.change_layers = change_layers
        self.time_series = {(row["site_id"], row["polarization"]): row for row in time_series}
        self.water_levels = {record["site_id"]: record for record in water_levels}
        # A combined report can be listed in the guide. A local answer needs
        # a result explicitly scoped to the selected site.
        self.experiments = {row["site_ids"][0]: row for row in experiments if len(row["site_ids"]) == 1}
        for key, observation in self.observations.items():
            row = catalogue.seed[key]
            if any(entry["science_sha256"] != row["channels"][pol]["science_sha256"]
                   for pol, entry in observation["pol"].items()):
                raise ValueError(f"Rebuild the matrix measurements for {key}")
        for layer in change_layers:
            key = (layer["earlier_id"], layer["later_id"])
            pair = next(item for item in self.pairs if (item["earlier_id"], item["later_id"]) == key)
            channel = layer["polarization"]
            if (pair["site_id"] != layer["site_id"] or pair["channels"][channel]["paired_pixels"] != layer["paired_native_pixels"]
                    or layer["image_epsg"] != 3857 or any(catalogue.seed[ident]["channels"][channel]["image_bounds"] != layer["image_bounds"]
                                                         for ident in key)):
                raise ValueError(f"Rebuild the change layer for {key}")

    def _rows(self, site_id, state, ids, all_dates=False):
        if site_id and (all_dates or site_id != state.get("site_id")):
            return [row for row in self.catalogue.seed.values() if row.get("site_id") == site_id]
        candidates = self.catalogue.known(ids)
        if site_id:
            candidates += [row for row in self.catalogue.seed.values() if row.get("site_id") == site_id]
        return [row for row in {row["id"]: row for row in candidates}.values()
                if (not site_id or row.get("site_id") in (site_id, None))
                and (not state.get("start") or row["acquired_at"][:10] >= state["start"])
                and (not state.get("end") or row["acquired_at"][:10] <= state["end"])]

    def suggest_site(self, topic):
        candidates = [site for site in self.catalogue.sites.values() if site["topic"] == topic]
        def rank(site):
            site_id = site["id"]
            paired = sum(pair["site_id"] == site_id for pair in self.pairs)
            markers = len(site.get("review_points", []))
            return (bool(self.water_agreement(site_id)), paired, site_id in self.experiments, markers) if topic == "wetlands" else (
                site_id in self.experiments, markers, paired)
        return max(candidates, key=rank, default=None)

    def water_agreement(self, site_id, channel="HH", observations=None):
        ids = {row["id"] for row in observations} if observations is not None else None
        return next((layer for layer in self.catalogue.data.get("recognition_layers", [])
                     if layer["site_id"] == site_id and layer["polarization"] == channel
                     and (ids is None or layer["observation_id"] in ids)
                     and any(entry["id"] == "water-agreement" for entry in layer["classes"])
                     and any(feature["properties"].get("class_id") == "water-agreement"
                             for feature in (layer.get("outlines") or {}).get("features", []))), None)

    def change_layer(self, rows, channel):
        ids = {row["id"] for row in rows}
        matches = [layer for layer in self.change_layers if layer["polarization"] == channel
                   and {layer["earlier_id"], layer["later_id"]} <= ids]
        return max(matches, key=lambda row: row["later_date"], default=None)

    def answer_layer(self, text, state, ids, site, named_site):
        rows = self._rows(site["id"] if site else None, state, ids, all_dates=named_site)
        dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text))
        if dates:
            rows = [row for row in rows if row["acquired_at"][:10] in dates]
        specified = re.search(r"\b(HH|HV|VH|VV)\b", text, re.I)
        channel = specified[0].upper() if specified else state.get("channel") or "HH"
        return self.change_layer(rows, channel)

    def answer(self, intent, text, state, ids, site, named_site=False):
        site_id = site["id"] if site else None
        research = self.experiments.get(site_id)
        if intent == "recognition":
            if research:
                return research["plain_result"], research["evidence_ids"]
            if site_id == "eloi-bay-nisar":
                context, evidence = self._interpretation("oil", self._rows(site_id, state, ids, all_dates=named_site), site)
                return "Eloi Bay has a separately timed reference outline. A site-specific model result is unavailable. " + context, evidence
            return "Open Curuai, Shark River Slough, or the Umm Al Quwain coast and choose Recognition tests in Source audit to see how well the research models performed.", []
        if intent == "legend":
            return self._legend(site, state)
        if intent == "radar_basics":
            return ("A radar satellite sends a signal toward Earth and measures how much comes back. The dark parts of a radar image sent less back. "
                    "Rough surfaces, vegetation and viewing direction all affect the return. Start with one image, then compare the same radar channel on another date.",
                    ["asf-nisar-gcov", "nisar-polarimetry"])
        if intent == "terrain_shadow":
            return ("Radar looks sideways. A ridge can block the signal from reaching the slope behind it, leaving a radar shadow. "
                    "A dark measured pixel can also come from a smooth surface that sent little energy back. Check the viewing direction, the terrain and the provider quality mask before deciding what caused the darkness.",
                    ["asf-radar-geometry", "asf-nisar-gcov"])
        rows = self._rows(site_id, state, ids, all_dates=named_site)
        explicit_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text))
        if explicit_dates:
            rows = [row for row in rows if row["acquired_at"][:10] in explicit_dates]
        preferred = [pol for pol in ("HH", "HV", "VV", "VH") if re.search(rf"\b{pol}\b", text, re.I)]
        channel = (preferred or [state.get("channel") or "HH"])[0]
        if intent == "water_level":
            context, sources = self._gauge_context(site, explicit_dates)
            if context:
                return context, sources
            return "I have no verified gauge reading for that place and date. The radar image shows how strongly the signal returned. Gauge readings are needed for water height.", self._sources(rows)
        if intent == "time_series":
            return self._series_answer(site, rows, channel)
        if intent in ("compare", "change"):
            decline = bool(re.search(r"\b(?:darker|darken\w*|weaker|weaken\w*|declin\w*|fell|fall)\b", text, re.I))
            pair, comparison = self._comparison(rows, channel, decline=decline)
            if pair:
                return comparison, ["asf-nisar-gcov", *pair["evidence_ids"]]
            days = sorted({row["acquired_at"][:10] for row in rows if row["id"] in self.observations})
            if explicit_dates - set(days):
                return "The matrix has no verified radar crop on all the dates you asked for. Search those dates in Manual, then load matching scientific rasters before comparing them.", self._sources(rows)
            if not site and not rows and self.pairs:
                pair = self.pairs[0]
                return "The best time comparison is at Lago Grande de Curuai. Open that site and compare 13 August with 12 October 2026 in HH or HV. These images cover the same area and were taken from the same direction.", pair["evidence_ids"]
            if days and self._has_channel_pair(rows, channel):
                return comparison, self._sources(rows)
            what = site["name"] if site else "This selection"
            dates = f"one measured {pretty_date(days[0])} radar scene" if len(days) == 1 else f"{len(days)} dates with measured radar pixels"
            alternatives = [pair for pair in self.pairs if pair["site_id"] != site_id]
            suggestion = " Try Curuai for a measured August–October comparison." if alternatives else ""
            return f"{what} has {dates} and no verified like-for-like {channel} pair here. To see change, use another date with the same channel, viewing geometry and overlapping radar pixels.{suggestion}", self._sources(rows)

        if intent == "polarization":
            return self._polarization(rows, state, site, preferred)
        if intent == "coverage":
            return self._coverage(rows, state, channel)
        if research and intent in ("wetlands", "oil") and re.search(r"\b(?:model|accuracy|detect\w*|recogniz\w*|classif\w*)\b", text, re.I):
            return research["plain_result"], research["evidence_ids"]
        if intent == "drought":
            context, sources = self._gauge_context(site, explicit_dates, brief=True)
            if context:
                return f"{DROUGHT_MECHANISM} {context}", [*sources, "drought-indicators", "asf-nisar-gcov"]
        return self._interpretation(intent, rows, site)

    def _legend(self, site, state):
        if not site:
            return "Blue and green are the background map's water and land colours. A gray radar image appears over the background when you select a measured scene. Dark gray means the radar received a weaker return.", ["asf-nisar-gcov"]
        reference = site.get("event_reference")
        if reference and not state.get("active_id"):
            return ("The red shapes trace possible slicks mapped by Greenpeace from satellite radar. "
                    "Esri World Imagery provides the background geography.", [reference["evidence_id"]])
        active_change = state.get("change_layer_id")
        change = next((layer for layer in self.change_layers if layer["id"] == active_change and layer["site_id"] == site["id"]
                       and layer["polarization"] == state.get("channel") and state.get("active_id") in (layer["earlier_id"], layer["later_id"])), None)
        if change:
            evidence_check = ("Check a separate near-time observation to investigate oil." if site["topic"] == "oil_spill"
                              else "Check independent water observations to investigate flooding or drought.")
            return ("On this change map, orange marks places where the radar return rose by about a factor of two or more. Olive marks places where it fell to about half or less. "
                    f"The gray image is the selected date. {evidence_check}", ["asf-nisar-gcov", *change["evidence_ids"]])
        if site.get("review_points"):
            if any(point["topic"] == "wetlands" for point in site["review_points"]):
                markers = "The red diamond points to a dark offshore feature to inspect. The orange circle marks the mapped tidal wetland."
            else:
                markers = "The red diamond marks a location documented in the inspection record."
            return (markers + " " +
                    "Click a point for its evidence. The gray image shows the measured return, with weaker returns drawn darker.",
                    ["asf-nisar-gcov", site["evidence_id"], *[point["evidence_id"] for point in site["review_points"]]])
        outline = "red" if site["topic"] == "oil_spill" else "orange"
        return (f"The {outline} outline marks the study area. Inside the gray radar image, darker pixels have weaker radar returns. "
                "Blue and green outside the image provide background water and land context.", ["asf-nisar-gcov", site["evidence_id"]])

    def _gauge_context(self, site, dates, brief=False):
        record = self.water_levels.get(site["id"]) if site else None
        if not record:
            return None, []
        available = {reading["date"]: reading for reading in record["readings"]}
        if dates - available.keys():
            return None, []
        readings = [available[day] for day in sorted(dates or available.keys())]
        phrase = f"The {record['water_body']} gauge at {record['station']} recorded "
        if len(readings) == 1:
            reading = readings[0]
            phrase += f"{reading['value_cm']} cm on {pretty_date(reading['date'])}, measured from the station's gauge zero."
        else:
            first, last = readings[0], readings[-1]
            fall = (first['value_cm'] - last['value_cm']) / 100
            if brief:
                phrase += f"a {fall:.2f} m fall between the two dates."
            else:
                phrase += (f"{first['value_cm']} cm on {pretty_date(first['date'])} and {last['value_cm']} cm on {pretty_date(last['date'])}, "
                           f"a fall of {fall:.2f} m at that station. The later reading was {last['value_cm'] - record['low_water_reference_cm']} cm above its low-water reference.")
        return phrase + " This is a river gauge for the region. Lago Grande de Curuai has its own lake level.", [
            *[reading['evidence_id'] for reading in readings], "curuai-floodplain-hydrology"]

    def explain_selection(self, site, rows, topic, polarizations, prefer_change=False, prefer_series=False):
        ready = [row for row in rows if row["id"] in self.observations]
        if not ready:
            if rows:
                return "The boxes show where the satellite looked. Open Source audit to load a radar image.", ["asf-nisar-gcov"]
            return "I found no matching scenes in that area and date range. Try a wider date range in Manual.", []
        channel = (polarizations or ["HH"])[0]
        if polarizations and all(channel not in self.observations[row["id"]]["pol"] for row in ready):
            if any(row["channels"].get(channel, {}).get("status") in PROVIDER_LISTED_CHANNELS for row in rows):
                return f"{channel} is listed. Open Source audit to download the image, or choose a measured channel in Acquisitions.", self._sources(rows)
            return f"This selection has no verified {channel} image. Open Acquisitions and choose a channel marked Verified.", self._sources(rows)
        if prefer_series:
            return self._series_answer(site, rows, channel, selection=True)
        if prefer_change:
            layer = self.change_layer(rows, channel)
            if layer:
                return f"Radar change compares the same ground on two dates in {channel}.", layer["evidence_ids"]
        active = ready[0]
        review = next((layer for layer in self.catalogue.data.get("recognition_layers", ())
                       if layer["observation_id"] == active["id"] and layer["polarization"] == channel), None)
        if review:
            classes = {entry["id"] for entry in review["classes"]}
            meaning = next((phrase for label, phrase in (
                ("water-agreement", "Solid orange outlines mark exposed water that radar and a separate image taken with light both picked out."),
                ("radar-agreement", "Solid red marks dark water inside NOAA’s dashed possible-oil boundary. The material is unconfirmed."),
                ("slick-reference", "Dashed red shows a later slick outline. Orange shows mapped mangroves."),
                ("noaa-reference", "Dashed red traces NOAA’s later possible-oil outline."),
                ("optical-water", "Dashed orange shows water in the later optical image. Radar noise prevented a check."),
            ) if label in classes), None)
            if meaning:
                return meaning, review["evidence_ids"]
        if topic == "oil_spill":
            return "Oil can smooth the waves and make a dark patch. Calm water can look similar.", self._sources(ready, "oil-field-study")
        if topic == "wetland_drought":
            return "Look for water retreat across comparable seasons. Rainfall or gauge records help tell whether the retreat is unusual.", self._sources(ready, "drought-indicators")
        return "Open water often looks dark. Choose another date in Acquisitions to compare.", self._sources(ready, "wetland-field-study")

    def _comparison(self, rows, channel, decline=False):
        ids = {row["id"] for row in rows}
        pairs = [pair for pair in self.pairs if {pair["earlier_id"], pair["later_id"]} <= ids]
        for pair in sorted(pairs, key=lambda item: item["later_date"], reverse=True):
            values = pair["channels"].get(channel)
            if not values:
                continue
            total = values["paired_pixels"]
            early, late = pretty_date(pair["earlier_date"]), pretty_date(pair["later_date"])
            topic = self.catalogue.sites[pair["site_id"]]["topic"]
            next_check = ("Compare with weather and the inspection record before attributing a change to oil."
                          if topic == "oil_spill" else "Confirm flooding or water loss with separate water observations.")
            effect = "fell to about half or less" if decline else "roughly doubled or more"
            portion = values["darker_at_least_3db_pct" if decline else "brighter_at_least_3db_pct"]
            return pair, (f"Compare {early} and {late} in {channel}. The echo {effect} at {portion:.1f}% of the {total:,} shared pixels. {next_check}")
        return None, f"A measured pair exists for a different channel. Compare identical channels across dates. Switch from {channel} to an available measured channel in Acquisitions."

    def _series_answer(self, site, rows, channel, selection=False):
        series = self.time_series.get((site["id"], channel)) if site else None
        ids = {row["id"] for row in rows}
        points = sorted((point for point in (series or {}).get("points", []) if point["observation_id"] in ids),
                        key=lambda point: point["acquired_at"])
        if len(points) < 2:
            return (f"The chart needs at least two measured {channel} images covering the same ground pixels. "
                    "Open Acquisitions at Curuai or Marañón to see an available series.", self._sources(rows))
        first_new_release = next((after for before, after in zip(points, points[1:])
                                  if before.get("processing_release") and after.get("processing_release")
                                  and before["processing_release"] != after["processing_release"]), None)
        if first_new_release:
            changed = pretty_date(first_new_release["acquired_at"])
            if series.get("reference_evidence_id") == "uae-falcon-eye-reference":
                matched = [point for point in points if point["acquired_at"] < first_new_release["acquired_at"]]
                if len(matched) == 2 and matched[0]["processing_release"] == matched[1]["processing_release"]:
                    earlier, later = matched
                    difference = later["value"] - earlier["value"]
                    direction = "weaker" if difference < 0 else "stronger"
                    return (f"Inside the same offshore slick reference outline, the {channel} radar echo on "
                            f"{pretty_date(later['acquired_at'])} was {abs(difference):.2f} dB {direction} "
                            f"than on {pretty_date(earlier['acquired_at'])}. "
                            f"The {changed} image uses a different processing release, so the chart has a break. "
                            "Calm water can also weaken the echo.",
                            ["asf-nisar-gcov", *series["evidence_ids"]])
            return (f"The chart shows {channel} radar echoes from the same ground pixels. "
                    f"The {changed} image uses a different processing release, so the chart has a break there. "
                    "Select a date in Acquisitions to see its radar image.",
                    ["asf-nisar-gcov", *series["evidence_ids"]])
        if selection:
            return (f"The chart in Acquisitions compares {channel} echoes over the same ground. Select a date to open its image.",
                    ["asf-nisar-gcov", *series["evidence_ids"]])
        start, end = points[0], points[-1]
        statistic = "Each dot shows the middle radar return over the same usable ground pixels." if "median" in series["statistic"].lower() else "Each dot summarizes radar return over the same usable ground pixels."
        return (f"{statistic} In {channel} it went from {start['value']:.2f} dB to {end['value']:.2f} dB. "
                "A higher dot means a stronger echo. Select a date in Acquisitions to open its image.",
                ["asf-nisar-gcov", *series["evidence_ids"]])

    def _has_channel_pair(self, rows, channel):
        ids = {row["id"] for row in rows}
        return any({pair["earlier_id"], pair["later_id"]} <= ids and channel not in pair["channels"] for pair in self.pairs)

    def _scene(self, rows, state):
        active = state.get("active_id")
        return next((row for row in rows if row["id"] == active),
                    max(rows, key=lambda row: row["acquired_at"], default=None))

    def _polarization(self, rows, state, site, preferred):
        row = self._scene(rows, state)
        if not row:
            return "Choose a study area and date to see its channels. HH and VV send and receive the same orientation. HV and VH send one orientation and receive the other.", ["nisar-polarimetry"]
        measured = [pol for pol, meta in row["channels"].items() if meta["status"] == MEASURED]
        advertised = [pol for pol, meta in row["channels"].items() if meta["status"] in PROVIDER_LISTED_CHANNELS]
        unknown = [pol for pol in preferred if row["channels"].get(pol, {}).get("status") == "unknown"]
        missing = [pol for pol in preferred if pol not in measured and pol not in advertised and pol not in unknown]
        summary = f"For {pretty_date(row['acquired_at'])}, the measured channels here are {' and '.join(measured)}." if measured else "This scene has no measured channel loaded in the app yet."
        if advertised:
            summary += f" {' and '.join(advertised)} {count_word(len(advertised), 'is', 'are')} listed by the provider and still {count_word(len(advertised), 'needs', 'need')} a scientific download."
        if missing:
            summary += f" {' and '.join(missing)} {count_word(len(missing), 'is', 'are')} unavailable for this scene."
        if unknown:
            summary += f" The provider has yet to verify {' and '.join(unknown)} for this scene."
        if "HH" in measured:
            summary += " Start with HH to see contrast across the scene."
        if "HV" in measured and (site or {}).get("topic") == "wetlands":
            summary += " Switch to HV to see how surfaces and vegetation change the radar signal's orientation. Compare the same channel across dates."
        return summary, self._sources([row], "nisar-polarimetry")

    def _coverage(self, rows, state, channel):
        row = self._scene(rows, state)
        measured = self.observations.get(row["id"], {}).get("pol", {}).get(channel) if row else None
        if not measured:
            return f"The selection has no verified {channel} pixel measurements on this date. A box in the map can show the scene location. Open Acquisitions to find a measured channel, or Source audit to prepare a download.", self._sources([row] if row else [])
        valid, total = measured["valid_pixels"], measured["total_pixels"]
        return (f"This {channel} crop has radar readings in {valid:,} of {total:,} pixels ({100 * valid / total:.1f}%). "
                "That covers the displayed crop. Transparent areas have no display value. Dark pixels within the radar image have a weak return. Source audit provides the measured GeoTIFF."), self._sources([row])

    def _interpretation(self, intent, rows, site):
        context, sources = SITE_CONTEXT.get(site["id"] if site else None, ("", ()))
        known = {item["id"] for item in (*SCIENCE_SOURCES, *self.catalogue.data["evidence"])}
        sources = [source for source in sources if source in known]
        context = f" {context}" if context else ""
        if intent == "oil":
            reference = (site or {}).get("event_reference")
            if reference and not rows:
                return ("Oil can smooth tiny waves, so less radar bounces back and the patch looks dark. Calm water can look dark too. "
                        "The red outlines come from Greenpeace’s 10 September radar interpretation, drawn over Esri imagery. "
                        "The NISAR catalogue has no matching image for that date and area.",
                        [reference["evidence_id"], "emsa-slick-context"])
            caveat = (" This image lacks a per-pixel estimate of instrument noise." if site and site["id"] == "gulf-eugene-nisar"
                      else " A separate observation helps confirm what’s on the water.")
            return (OIL_MECHANISM + caveat,
                    [*self._sources(rows, "oil-field-study"), "emsa-slick-context", *sources])
        if intent == "drought":
            return DROUGHT_MECHANISM + context, [*self._sources(rows, "drought-indicators"), *sources]
        return (WETLAND_MECHANISM + " A gauge or field observation helps check flooding under plants.",
                [*self._sources(rows, "wetland-field-study"), *sources])

    @staticmethod
    def _sources(rows, extra=None):
        return list(dict.fromkeys(x for x in [*[r.get("evidence_id") for r in rows], extra, "asf-nisar-gcov"] if x))
