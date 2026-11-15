"""Retrieve page-linked summaries of the NISAR Ecosystems ATBD."""
import json
import re


ATTRIBUTED = re.compile(r"\b(?:atbd|algorithm theoretical basis|ecosystems (?:document|design|algorithm)|siqueira)\b")
NISAR_EXPLANATION = re.compile(r"\bnisar\b")
SCOPE_TERMS = re.compile(r"\b(?:open water|inundat\w*|wetlands?|vegetation|biomass|disturbance|agriculture|crop\w*|terrain correction)\b")
CURRENT_APP = re.compile(r"\b(?:this app|sartainty|here|current (?:map|view|site)|our (?:model|app|algorithm))\b")


class EcosystemsKnowledge:
    def __init__(self, path):
        self.document = json.loads(path.read_text(encoding="utf-8"))
        self.entries = {entry["id"]: entry for entry in self.document["entries"]}
        self.matchers = {
            key: [(re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)"), 1 + len(term.split()) ** 2)
                  for term in item["terms"]]
            for key, item in self.entries.items()
        }

    def sources(self):
        return [{"id": self.source_id(key), "title": f"NISAR Ecosystems ATBD · {item['title']}",
                 "kind": "Science design document", "url": f"{self.document['url']}#page={item['pages'][0]}",
                 "notes": f"{item['section']} · pp. {item['pages'][0]}–{item['pages'][-1]} · {self.document['date']}",
                 "source_sha256": self.document["pdf_sha256"]}
                for key, item in self.entries.items()]

    @staticmethod
    def source_id(key):
        return f"nisar-ecosystems-atbd-{key}"

    def retrieve(self, text):
        text = text.replace("-", " ").replace("–", " ")
        specified = ATTRIBUTED.search(text)
        if not specified and (not NISAR_EXPLANATION.search(text) or not SCOPE_TERMS.search(text) or CURRENT_APP.search(text)):
            return []
        # Deliberately scoped to this document's ecosystems algorithm topics.
        if re.search(r"\b(?:oil spills?|magnitude of earthquakes?|ship detection|tsunami warning)\b", text):
            return ["overview"] if specified else []
        if re.search(r"\b(?:open water|flooded vegetation|inundat\w*)\b", text) and re.search(r"\b(?:thresholds?|cutoffs?|table 3\.3)\b", text):
            return ["water-thresholds"]
        if re.search(r"\b3\.3\b", text) and not SCOPE_TERMS.search(text):
            return ["water-vegetation", "water-classes", "water-time"]
        scores = [(sum(weight for term, weight in tests if term.search(text)), key)
                  for key, tests in self.matchers.items()]
        ranked = sorted(((score, key) for score, key in scores if score > 0), key=lambda item: -item[0])
        if not ranked:
            return ["overview"] if specified else []
        best = ranked[0][0]
        return [key for score, key in ranked if score >= max(2, best / 2)][:3]

    def choices(self, ids):
        return [{"id": key, "section": self.entries[key]["section"], "subject": self.entries[key]["title"],
                 "summary": self.entries[key]["answer"]} for key in ids]

    def answer(self, key, topic, *, model_used=False):
        entry = self.entries[key]
        return {"action": {"type": "answer", "topic": topic},
                "message": entry["answer"], "evidence_ids": [self.source_id(key)],
                "answer_model": "ecosystems_atbd", "answer_kind": key, "model_used": model_used}
