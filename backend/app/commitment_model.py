import json
import os
from functools import lru_cache
from pathlib import Path

LABELS = ("explicit_promise", "implicit_commitment", "intention", "suggestion", "request_assignment", "non_commitment")
COMMITMENT_LABELS = LABELS[:2]
REVIEW_THRESHOLD = 0.35
BASE_MODEL = "jhu-clsp/ettin-encoder-68m"
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "data" / "phase3" / "classifier"
WORKSPACE_MODEL = DEFAULT_MODEL.with_name("icsi-recall")


def configured_model():
    if configured := os.getenv("PROMISE_KEEPER_CLASSIFIER"):
        return Path(configured)
    if (DEFAULT_MODEL / "config.json").is_file() or not (WORKSPACE_MODEL / "config.json").is_file():
        return DEFAULT_MODEL
    return WORKSPACE_MODEL


def commitment_score(scores):
    return sum(scores[label] for label in COMMITMENT_LABELS)


def routes_to_review(label, scores):
    threshold = trained_review_threshold(str(configured_model()))
    if threshold is not None:
        return commitment_score(scores) >= threshold
    return label in COMMITMENT_LABELS or commitment_score(scores) >= REVIEW_THRESHOLD


@lru_cache(maxsize=4)
def trained_review_threshold(model_path):
    config = Path(model_path) / "config.json"
    if config.is_file():
        return json.loads(config.read_text(encoding="utf-8")).get("promise_review_threshold")
    return None


def classifier_inputs(items, adjacent_context=False):
    if adjacent_context:
        context = [
            "\n".join(f"{label}: {item[field]}" for label, field in (
                ("Previous", "context"), ("Following", "next_context")
            ) if item.get(field))
            for item in items
        ]
        return context, [item["quote"] for item in items]
    return [item.get("context", "") for item in items], [item["quote"] for item in items]


@lru_cache(maxsize=1)
def classifier():
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    path = configured_model()
    if not (path / "config.json").is_file():
        raise RuntimeError("Train and export the Phase 3 classifier before analyzing transcripts.")
    model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).eval()
    if tuple(model.config.id2label[i] for i in range(model.config.num_labels)) != LABELS:
        raise RuntimeError("The classifier uses a different label order.")
    return AutoTokenizer.from_pretrained(path, local_files_only=True), model


def predict(items):
    import torch

    tokenizer, model = classifier()
    results = []
    with torch.inference_mode():
        for offset in range(0, len(items), 16):
            first, second = classifier_inputs(
                items[offset:offset + 16],
                adjacent_context=getattr(model.config, "promise_adjacent_context", False),
            )
            features = tokenizer(first, second, max_length=160, truncation=True, padding=True, return_tensors="pt")
            for scores in model(**features).logits.softmax(dim=-1).tolist():
                results.append(dict(zip(LABELS, scores, strict=True)))
    return results
