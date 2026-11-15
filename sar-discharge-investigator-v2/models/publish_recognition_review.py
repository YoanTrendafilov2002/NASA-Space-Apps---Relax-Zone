"""Publish brief, source-linked reviews from the frozen recognition tests."""
from pathlib import Path
import json


APP = Path(__file__).resolve().parents[1]


def read_summary(topic):
    path = APP / "results" / "recognition" / topic / "summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def metric(label, value, unit=""):
    return {"label": label, "value": value, "unit": unit}


def main():
    wetlands = read_summary("wetlands")
    oil = read_summary("oil")
    variant = "HH and HH−HV logistic with local HH texture"
    august, october, loss = wetlands["metrics"]
    august_overlap = august["models"][variant]["iou"] * 100
    october_overlap = october["models"][variant]["iou"] * 100
    october_baseline = october["models"]["QGIS HH threshold"]["iou"] * 100
    loss_recall = loss["models"][variant]["recall"] * 100
    uae = oil["cases"]["held_out"]["recognition"]
    reference_cells = uae["proxy_cells"]
    assessed = uae["eligible_proxy_cells"]
    hits = uae["overlap_cells"]
    eligible = uae["proxy_eligible_fraction"] * 100
    everglades = json.loads((APP / "results" / "recognition" / "wetlands" / "everglades" / "research-report.json").read_text(encoding="utf-8"))
    hh, dual = (row["IoU"] * 100 for row in everglades["metrics"] if row["fold"] == "all held-out")
    maranon = json.loads((APP / "results" / "recognition" / "oil" / "maranon-qgis-review.json").read_text(encoding="utf-8"))["selected_review"]
    experiments = [
        {
            "id": wetlands["id"], "title": "Can the model recognize exposed water?",
            "site_ids": ["curuai-nisar"], "status": "completed", "outcome": "research_only",
            "plain_result": (f"Water mapped by the model overlapped the reference much better in August ({august_overlap:.0f}%) than October ({october_overlap:.0f}%). "
                             "The August model had limited value on the later scene."),
            "scope": (f"Fitted on {wetlands['training_cells']:,} August cells with NISAR HH and HV. Tested on separate areas in August "
                      "and the same withheld areas in October. A clear optical image supplied the exposed-water reference."),
            "metrics": [metric("August overlap · model", august_overlap, "%"),
                        metric("October overlap · model", october_overlap, "%"),
                        metric("October overlap · simple cutoff", october_baseline, "%"),
                        metric("Reference water loss found", loss_recall, "%")],
            "notes": ["Overlap is intersection over union. These measurements address exposed water in this one floodplain. For under-canopy flooding or drought, use independent evidence.",
                      "The October and paired tests used the August-fitted model without retuning."],
            "evidence_ids": ["nisar-curuai-20260813-source", "nisar-curuai-20261012-source", "wetland-field-study", "curuai-august-optical-labels", "curuai-october-optical-labels"],
        },
        {
            "id": "gulf-oil-recognition-transfer", "title": "Can the model identify a reported slick?",
            "site_ids": ["uae-coastal-review"], "status": "completed", "outcome": "research_only",
            "plain_result": (f"The oil screen evaluated {assessed:,} of {reference_cells:,} cells in the independent slick outline "
                             f"and flagged {hits} inside it. Quality checks left most of the outline unevaluable."),
            "scope": "Calibrated on NISAR HH measurements off Oman. Held the settings fixed for the reported GEM NO. 2 spill near Umm Al Quwain.",
            "metrics": [metric("Slick reference cells evaluated", eligible, "%"),
                        metric("Reference cells with a matching flag", hits, "cells"),
                        metric("Cells passed by quality checks", assessed, "cells")],
            "notes": ["The reference outline was mapped about 39 minutes after the NISAR pass. A public incident report confirms the spill. Wind, waves, timing and signal quality affect this screen.",
                      "The offshore map marker is a location to inspect visually. The screening method issued no match inside the eligible reference cells."],
            "evidence_ids": ["uae-gem-incident", "uae-gem-reference", "nisar-uae-20261021-source", "oil-field-study"],
        },
        {
            "id": "everglades-emergent-marsh-test", "title": "Does a second channel help in the marsh?",
            "site_ids": ["everglades-nisar"], "status": "completed", "outcome": "research_only",
            "plain_result": (f"At Shark River Slough, adding HV information raised overlap with the modeled marsh water reference from {hh:.0f}% to {dual:.0f}%. "
                             "The test covered one date in this marsh."),
            "scope": (f"NISAR HH and HV on 27 October, grouped into 400 m cells. Spatial testing used {everglades['assessed_cells']:,} cells. "
                      f"The radar quality rules withheld {everglades['radar_abstentions']:,} of the {everglades['eligible_cells']:,} depth and habitat reference cells."),
            "metrics": [metric("Overlap · HH only", hh, "%"), metric("Overlap · HH and HH–HV", dual, "%"),
                        metric("Cells assessed", everglades["assessed_cells"], "cells")],
            "notes": ["EDEN estimates depth between water-level gauges. The original test used 0 cm versus at least 10 cm in mapped freshwater emergent wetlands.",
                      "This site-specific test uses different labels and a coarser analysis grid than Curuai."],
            "evidence_ids": ["nisar-everglades-20261027-source", "everglades-eden-study", "everglades-nwi"],
        },
        {
            "id": "maranon-inspection-area-check", "title": "Could the river screen evaluate the inspection area?",
            "site_ids": ["maranon-nisar"], "status": "completed", "outcome": "research_only",
            "plain_result": (f"The river screen evaluated {maranon['assessable_water_cells_within_radius']} nearby water cells and identified no connected dark patch. "
                             "The inspection area itself fell outside the usable part of that screen."),
            "scope": "Compared 24 February and 8 March NISAR HH around the OEFA inspection location, using 60 m analysis cells and a 40 m exclusion near river banks.",
            "metrics": [metric("Water cells evaluated within 1 km", maranon["assessable_water_cells_within_radius"], "cells"),
                        metric("Connected dark patches within 1 km", maranon["connected_dark_patches_within_radius"], "patches"),
                        metric("Evaluated overlap with inspection area", maranon["assessable_overlap_with_inspection_m2"], "m²")],
            "notes": ["OEFA documented the incident in the field. Its inspection area gives a location and has no simultaneous slick outline for scoring radar detections.",
                      "The displayed 20 m change layer compares the measured radar return. The detection screen used a separate coarser grid and stricter quality conditions."],
            "evidence_ids": ["maranon-oefa-pontoon-4", "nisar-maranon-20260224-source", "nisar-maranon-20260308-source"],
        },
    ]
    sources = [
        *[{"id": f"curuai-{month}-optical-labels", "title": f"Curuai · {month.capitalize()} exposed-water reference",
           "kind": "Offline validation reference", "url": f"https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2A_21MXT_{day}_0_L2A",
           "notes": "Same-day clear optical classification and water-index agreement supplied approximate exposed-water labels. NISAR supplied the model predictors and map imagery."}
          for month, day in (("august", "20260813"), ("october", "20261012"))],
        {"id": "uae-gem-incident", "title": "Emirates News Agency · GEM NO. 2 spill and response",
         "kind": "Incident record", "url": "https://www.wam.ae/en/article/c3051mm-oil-spill-from-gem-no-tanker-contained",
         "notes": "The incident report confirms the spill and response near Umm Al Quwain."},
        {"id": "uae-gem-reference", "title": "Falcon Eye · separately mapped GEM NO. 2 outline",
         "kind": "Offline validation reference", "url": "https://www.ess-uae.me/post/satellite-oil-spill-monitoring-uae-umm-al-quwain",
         "notes": "The published outline represents the later reference observation. It was used to score the held-out NISAR screen."},
        {"id": "everglades-eden-study", "title": "USGS · EDEN regional water depth estimates",
         "kind": "Hydrologic model documentation", "url": everglades["depth_source"]["documentation"],
         "notes": "EDEN combines gauge observations, spatial interpolation and ground elevations. The marsh benchmark uses the resulting modeled water depths."},
        {"id": "everglades-nwi", "title": "US Fish and Wildlife Service · Wetlands inventory",
         "kind": "Habitat inventory", "url": everglades["inventory_source"]["documentation"],
         "notes": "Freshwater emergent marsh polygons define the evaluation domain. The mapped habitats do not give daily inundation."},
    ]
    path = APP / "data" / "nisar-experiments.json"
    path.write_text(json.dumps({"schema_version": 1, "experiments": experiments, "evidence": sources},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
