"""Plain-language scope of the selected NISAR acquisition."""
from datetime import date

from explorer_catalogue import POLARIZATIONS


def screen_profiles(recognition_rows):
    return {
        ident: {
            "mode": row["mode"],
            "no_assessable_water": row.get("counts_expected", {}).get("assessed_cells") == 0,
            "has_vegetation_reference": bool(row.get("vegetation_context")),
        }
        for ident, row in recognition_rows.items()
    }


def dataset_synopsis(catalogue, observation, polarization=None):
    """Use exact acquisition and review records. A site's topic is only a filter."""
    if not observation:
        return {"scope": "empty", "title": "Choose an acquisition",
                "looking_for": "Select a NISAR date to see which checks its data can support.", "evidence_ids": []}
    ident = observation["id"]
    site_id = observation.get("site_id")
    site = catalogue.sites.get(site_id, {})
    channels = observation.get("channels", {})
    channel = channels.get(polarization, {})
    prepared = [pol for pol in POLARIZATIONS if channels.get(pol, {}).get("science_url")]
    profile = catalogue.data.get("dataset_screen_profiles", {}).get(ident) or {}
    mode = profile.get("mode")
    if not mode:
        modes = catalogue.data.get("analysis_model_observations", {})
        mode = next((name for name in ("wetland", "oil") if ident in modes.get(name, ())), None)
    review = next((record for record in catalogue.data.get("recognition_layers", ())
                   if record["site_id"] == site_id and record["observation_id"] == ident
                   and record["polarization"] == (polarization or "HH")), {})
    classes = {item["id"] for item in review.get("classes", ())}
    acquired = observation.get("acquired_at") or ""
    try:
        day = date.fromisoformat(acquired[:10])
        stamp = f"{day.day} {day:%b %Y}"
    except (ValueError, TypeError):
        stamp = ""
    synopsis = {"dataset": " · ".join(filter(None, (site.get("name") or "NISAR acquisition", stamp)))}

    if mode == "oil":
        synopsis.update(scope="oil", title="Oil screening",
                        looking_for="Looks for possible slicks in dark patches on open water.",
                        using="Uses NISAR HH radar and compares each patch with water on both sides.",
                        why="Oil can smooth ripples and weaken the echo. Calm water can look similar.")
        if "radar-agreement" in classes:
            synopsis["result"] = "Some darker water overlapped a later NOAA possible-oil outline."
    elif mode == "wetland":
        limited = profile.get("no_assessable_water")
        synopsis.update(scope="wetland", title="Wetland check limited" if limited else "Wetland screening",
                        looking_for="Looks for open water and uncertain water edges.",
                        using="Uses NISAR HH and HV radar and checks for instrument noise.",
                        why="Smooth water reflects much of the radar signal away from the satellite.")
        if limited:
            synopsis["result"] = "The saved noise check found no usable radar cells for mapping open water."
            if {"slick-reference", "mangrove-inventory"} <= classes:
                synopsis["result"] += " Separate sources provide the slick and mangrove outlines."
        elif not profile.get("has_vegetation_reference"):
            synopsis["result"] = "Checking for water under plants needs a matched drier image and a vegetation map."
        if "water-agreement" in classes:
            synopsis["why"] = "The saved outlines show where radar and a same-day optical image agreed on open water."
    elif review.get("origin") == "habitat_reference":
        synopsis.update(scope="reference", title="Wetland reference",
                        looking_for="Shows where water may cover mapped marsh.",
                        using="Uses USGS water-depth estimates and a wetland inventory alongside NISAR radar.",
                        why="The depth and habitat records provide independent context for the radar image.")
    elif "optical-water" in classes:
        synopsis.update(scope="reference", title="Open-water reference",
                        looking_for="Shows where a later optical image mapped exposed water.",
                        using="Uses the later optical outline alongside NISAR radar.",
                        why="The radar noise check left this crop without a usable wetland estimate.")
    elif prepared:
        synopsis.update(scope="radar", title="Radar measurements",
                        looking_for="Shows how strongly land and water returned the radar signal on this date.",
                        using=f"Uses calibrated NISAR {' and '.join(prepared)} measurements.",
                        why="Oil and wetland screening need additional prepared inputs for this crop.")
    elif channel.get("preview_url") or observation.get("preview_url"):
        synopsis.update(scope="preview", title="Preview only",
                        looking_for="Compare how nearby images look to choose dates to investigate.",
                        using="Uses display previews. Oil and wetland screening require calibrated radar inputs.")
    else:
        synopsis.update(scope="catalogue", title="Catalogue only",
                        looking_for="This record identifies the acquisition. Add a preview or prepared radar image to inspect it.")
    if mode in ("oil", "wetland") and polarization in POLARIZATIONS and polarization != "HH":
        synopsis["using"] += " Switch to HH to run the check."
    sources = [channel.get("evidence_id"), observation.get("evidence_id"), *review.get("evidence_ids", ()),
               "wetland-v2-validation" if mode == "wetland" else None, "asf-nisar-gcov"]
    available = {entry["id"] for entry in catalogue.data.get("evidence", ())}
    synopsis["evidence_ids"] = list(dict.fromkeys(source for source in sources if source in available))[:4]
    return synopsis
