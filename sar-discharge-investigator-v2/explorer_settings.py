"""Source-backed workflow settings and mission design explanations."""
import json
import re


SETTINGS_REQUEST = re.compile(
    r"\b(?:settings?|configuration|parameters?|setup|set up|"
    r"which (?:nisar )?(?:product|data|band|channel|polarizations?|dates?|track|grid|quality checks?)|"
    r"what (?:nisar )?(?:product|data|band|channel|polarizations?|dates?|track|grid|quality checks?)|"
    r"how (?:far apart|many days apart|long between)|pair spacing)\b"
)
EARTH = re.compile(
    r"\b(?:solid earth|earthquakes?|co ?seismic|secular|transient (?:ground |surface )?(?:motion|deformation|displacement)|"
    r"short term ground deformation|ground (?:motion|deformation|displacement)|subsiden\w*|gunw|interferograms?)\b"
)
ICE = re.compile(r"\b(?:sea ice|ice drift|pack ice|marginal ice|drift buoys?|ice (?:motion|velocity|tracking))\b")
SPECIFIC_FIELD = {
    "timing": re.compile(r"\b(?:which dates|what dates|when|date range|pair spacing|how far apart|how many days|days apart|how long between|cadence|timing)\b"),
    "data": re.compile(r"\b(?:which|what) (?:nisar )?(?:product|data|band|channel|polarizations?)\b"),
    "geometry": re.compile(r"\b(?:which|what) (?:track|grid|geometry|look direction)\b"),
    "quality": re.compile(r"\b(?:quality checks?|coherence threshold|which corrections|what corrections)\b"),
}


def _normalise(text):
    return re.sub(r"[-‐‑–—]", " ", text.casefold())


def _phrase(text):
    return re.compile(r"(?<!\w)" + re.escape(_normalise(text)) + r"(?!\w)")


class UseCaseSettings:
    def __init__(self, path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.profiles = doc["cases"]
        self.by_id = {case["id"]: case for case in self.profiles}
        self.sources = doc["sources"]
        if (doc.get("schema_version") != 1 or len(self.by_id) != len(self.profiles)
                or any(case["availability"] not in {"in_app", "reference_only"}
                       or not case["evidence_ids"] or not case["fields"] for case in self.profiles)):
            raise ValueError("Invalid workflow settings catalogue.")
        self.aliases = sorted(
            ((alias, _phrase(alias), profile_id)
             for profile_id, case in self.by_id.items() for alias in case["aliases"]),
            key=lambda item: -len(item[0]),
        )

    def public_profiles(self):
        return [{key: value for key, value in case.items() if key not in {"aliases", "chat"}} for case in self.profiles]

    def case_for_text(self, text):
        return next((case_id for _, pattern, case_id in self.aliases if pattern.search(text)), None)

    def request(self, text, selected_case, topic, *, map_command):
        text = _normalise(text)
        named = self.case_for_text(text)
        field_id = next((key for key, pattern in SPECIFIC_FIELD.items() if pattern.search(text)), None)
        is_settings = bool(SETTINGS_REQUEST.search(text) or (selected_case and field_id and not map_command))
        if not is_settings and not (map_command and named and self.by_id[named]["availability"] == "reference_only"):
            return None
        case_id = named or selected_case or (topic if topic in self.by_id else None)
        if not case_id:
            return {"action": {"type": "answer", "topic": topic},
                    "message": "Open Use-case settings and pick a workflow, or ask for settings for a named use case.",
                    "answer_model": "use_case_settings", "answer_kind": "choose_use_case", "evidence_ids": [],
                    "model_used": False}
        case = self.by_id[case_id]
        field = next((field["value"] for field in case["fields"] if field["id"] == field_id), None)
        message = field or case["chat"]
        if case["availability"] == "reference_only":
            message += " Use-case settings has the source links for this reference workflow."
        return {"action": {"type": "answer", "topic": topic}, "message": message,
                "settings_case_id": case_id, "answer_model": "use_case_settings",
                "answer_kind": field_id or case_id, "evidence_ids": case["evidence_ids"], "model_used": False}


class MissionReferences:
    def __init__(self, path):
        index = json.loads(path.read_text(encoding="utf-8"))
        self.documents = {doc["id"]: doc for doc in index["documents"]}
        self.entries = {entry["id"]: (doc, entry) for doc in self.documents.values() for entry in doc["entries"]}
        if index.get("schema_version") != 1 or set(self.documents) != {"solid-earth", "sea-ice"}:
            raise ValueError("Invalid mission reference index.")
        self.matchers = {
            key: [(_phrase(term), 1 + len(term.split()) ** 2) for term in item["terms"]]
            for key, (_, item) in self.entries.items()
        }

    @staticmethod
    def source_id(key):
        return f"nisar-mission-atbd-{key}"

    def sources(self):
        results = []
        for key, (doc, entry) in self.entries.items():
            pages = entry.get("pages")
            url = f"{doc['url']}#page={pages[0]}" if pages else doc["url"]
            source = {"id": self.source_id(key), "title": f"{doc['title']} · {entry['title']}",
                      "kind": "Science design document", "url": url,
                      "notes": f"{entry['section']} · {doc['date']}"}
            if "sha256" in doc:
                source["source_sha256"] = doc["sha256"]
            results.append(source)
        return results

    def retrieve(self, text, selected_case=None):
        text = _normalise(text)
        ice = bool(ICE.search(text))
        earth = bool(EARTH.search(text))
        if not ice and not earth:
            ice = selected_case == "sea_ice_motion"
            earth = selected_case in {"coseismic", "transient", "secular"}
        if not ice and not earth:
            return []
        accepted = {"solid-earth": earth, "sea-ice": ice}
        scores = [(sum(weight for term, weight in tests if term.search(text)), key)
                  for key, tests in self.matchers.items() if accepted[self.entries[key][0]["id"]]]
        ranked = sorted(((score, key) for score, key in scores if score), key=lambda row: -row[0])
        if ranked:
            return [key for score, key in ranked if score >= ranked[0][0] / 2][:3]
        if ice and ICE.search(text):
            return ["ice-pairs"]
        if earth and EARTH.search(text):
            return [selected_case] if selected_case in {"coseismic", "transient", "secular"} else ["earth-los"]
        return []

    def choices(self, ids):
        return [{"id": key, "section": self.entries[key][1]["section"], "subject": self.entries[key][1]["title"],
                 "summary": self.entries[key][1]["answer"]} for key in ids]

    def answer(self, key, topic, *, model_used=False):
        doc, entry = self.entries[key]
        case_id = "sea_ice_motion" if doc["id"] == "sea-ice" else key if key in {"coseismic", "transient", "secular"} else None
        return {"action": {"type": "answer", "topic": topic}, "message": entry["answer"],
                "evidence_ids": [self.source_id(key)], "answer_model": "nisar_atbd", "answer_kind": key,
                "settings_case_id": case_id, "model_used": model_used}
