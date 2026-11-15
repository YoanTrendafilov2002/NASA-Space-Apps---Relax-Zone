"""Read the fitted FAQ router and classify questions without external dependencies."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import unicodedata


INTENTS = ("command", "compare", "change", "polarization", "coverage", "oil", "wetlands", "drought", "legend", "unknown")
FORMAT_VERSION = 1
TEMPERATURE = 1.7


def features(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).casefold()[:1000]
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " datevalue ", text)
    text = text.replace("polarisation", "polarization")
    tokens = re.findall(r"[^\W_]+", text)
    tokens = [
        "channelcode" if word in {"hh", "hv", "vv", "vh"} else "number" if word.isdecimal() else word
        for word in tokens
    ]
    return set(tokens) | {left + " " + right for left, right in zip(tokens, tokens[1:])}


class MatrixIntent:
    def __init__(self, path: Path):
        model = json.loads(Path(path).read_text(encoding="utf-8"))
        if model.get("format_version") != FORMAT_VERSION or tuple(model.get("intents", ())) != INTENTS:
            raise ValueError("The question model has an incompatible format.")
        self.vocabulary = frozenset(model["vocabulary"])
        self.classes = model["classes"]
        self.threshold = float(model["abstain_below"])
        self.temperature = float(model["temperature"])
        if not 0 < self.threshold <= 1 or not self.temperature > 0:
            raise ValueError("The question model has invalid confidence settings.")

    def rank(self, text: str) -> tuple[str, float]:
        known = sorted(features(text) & self.vocabulary)
        if not known:
            return "unknown", 0.0
        scores = {}
        for intent in INTENTS:
            cls = self.classes[intent]
            weights = cls["feature_log_weights"]
            scores[intent] = (
                cls["log_prior"]
                + len(known) * cls["unseen_log_likelihood"]
                + math.fsum(weights.get(feature, 0.0) for feature in known)
            )
        best = max(scores, key=scores.get)
        peak = scores[best]
        confidence = 1.0 / math.fsum(math.exp((score-peak)/self.temperature) for score in scores.values())
        return best, confidence

    def predict(self, text: str) -> tuple[str, float]:
        best, confidence = self.rank(text)
        return (best if confidence >= self.threshold else "unknown"), confidence
