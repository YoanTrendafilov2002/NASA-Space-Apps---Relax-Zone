"""Fit and evaluate Relax Zone's local FAQ question router."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from matrix_intent import FORMAT_VERSION, INTENTS, TEMPERATURE, MatrixIntent, features

ALPHA = 0.7
THRESHOLDS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
PASS_THROUGH = {"command", "unknown"}
SPLITS = ("train", "calibration", "test")


def load_examples(data: dict) -> dict:
    if data.get("format_version") != 1 or set(data["examples"]) != set(INTENTS):
        raise ValueError("Unsupported labelled question data.")
    examples = {split: [] for split in SPLITS}
    seen = set()
    for intent in INTENTS:
        for split in SPLITS:
            questions = data["examples"][intent][split]
            if len(questions) < 4:
                raise ValueError(f"Use at least four {split} examples for {intent}.")
            for question in questions:
                key = question.casefold().strip()
                if not key or key in seen:
                    raise ValueError(f"Duplicate or empty question: {question!r}")
                seen.add(key)
                examples[split].append((question, intent))
    return examples


def fit(train: list[tuple[str, str]]) -> dict:
    documents = Counter()
    by_class = {intent: Counter() for intent in INTENTS}
    vocabulary = set()
    for text, intent in train:
        fields = features(text)
        vocabulary.update(fields)
        by_class[intent].update(fields)
        documents[intent] += 1
    classes = {}
    for intent in INTENTS:
        counts = by_class[intent]
        normalizer = counts.total() + ALPHA * len(vocabulary)
        classes[intent] = {
            "log_prior": math.log(documents[intent] / len(train)),
            "unseen_log_likelihood": math.log(ALPHA / normalizer),
            "feature_log_weights": {field: math.log1p(counts[field] / ALPHA) for field in sorted(counts)},
        }
    return {"format_version": FORMAT_VERSION, "intents": list(INTENTS), "vocabulary": sorted(vocabulary), "classes": classes,
            "abstain_below": THRESHOLDS[0], "temperature": TEMPERATURE,
            "method": "Smoothed multinomial Naive Bayes using unique word unigrams and adjacent bigrams", "alpha": ALPHA}


def evaluate(ranked: list[tuple[str, str, str, float]], threshold: float) -> dict:
    confusion = {label: {guess: 0 for guess in INTENTS} for label in INTENTS}
    correct = answered = answered_correct = abstained = false_intercepts = 0
    errors = []
    for question, label, best, confidence in ranked:
        prediction = best if confidence >= threshold else "unknown"
        confusion[label][prediction] += 1
        correct += prediction == label
        abstained += confidence < threshold
        answered += prediction not in PASS_THROUGH
        answered_correct += prediction == label and prediction not in PASS_THROUGH
        false_intercepts += prediction not in PASS_THROUGH and label in PASS_THROUGH
        if prediction != label:
            errors.append({"question": question, "expected": label, "predicted": prediction, "confidence": round(confidence, 6)})
    return {"n": len(ranked), "correct": correct, "accuracy": correct/len(ranked),
            "answered": answered, "answered_correct": answered_correct,
            "answered_accuracy": answered_correct/answered if answered else None,
            "false_intercepts": false_intercepts, "abstained": abstained,
            "confusion": confusion, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", type=Path, default=APP/"data"/"matrix-assistant-training.json")
    parser.add_argument("--output", type=Path, default=APP/"data"/"matrix-assistant-model.json")
    args = parser.parse_args()
    payload = args.training.read_bytes()
    examples = load_examples(json.loads(payload))
    model = fit(examples["train"])
    model["training_sha256"] = hashlib.sha256(payload).hexdigest()
    stage = args.output.with_suffix(args.output.suffix + ".tmp")
    try:
        stage.write_text(json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        router = MatrixIntent(stage)
        rank = lambda rows: [(question, label, *router.rank(question)) for question, label in rows]
        validation = rank(examples["calibration"])
        thresholds = [(t, evaluate(validation, t)) for t in THRESHOLDS]
        threshold, _ = max(thresholds, key=lambda pair: (
            pair[1]["correct"] - 3*pair[1]["false_intercepts"],
            pair[1]["answered_correct"],
            pair[0],
        ))
        model["abstain_below"] = threshold
        model["scores"] = {split: evaluate(rank(examples[split]), threshold) for split in SPLITS}
        model["threshold_selection"] = "Calibration split only. Maximise correct routing minus triple false intercepts. Prefer correct answers, then higher threshold."
        model["confidence_limit"] = "Temperature-softened model score with an empirically chosen abstention threshold. Scores are not probabilities of correctness."
        stage.write_text(json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        stage.replace(args.output)
    finally:
        stage.unlink(missing_ok=True)
    print(f"Learned {len(model['vocabulary'])} features from {len(examples['train'])} examples. Abstain below {threshold:.2f}.")
    for split, report in model["scores"].items():
        print(f"{split}: {report['correct']}/{report['n']} correct, {report['false_intercepts']} command/unknown questions intercepted")


if __name__ == "__main__":
    main()
