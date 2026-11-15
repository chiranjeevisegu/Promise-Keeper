"""Fine-tune Ettin on reviewed labels and export merged weights for FastAPI."""
import argparse
import json
from pathlib import Path

from app.commitment_model import BASE_MODEL, DEFAULT_MODEL, LABELS, classifier_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="Resume the latest checkpoint for this output directory.")
    parser.add_argument("--balanced", action="store_true", help="Use capped inverse-frequency class weights.")
    parser.add_argument("--initialize-from", type=Path, help="Continue from a local exported Ettin classifier.")
    parser.add_argument("--review-beta", type=float, choices=(1., 2.), default=1., help="Optimize validation F1 or recall-weighted F2.")
    parser.add_argument("--selection-metric", choices=("macro_f1", "commitment_ap"), default="macro_f1")
    parser.add_argument("--adjacent-context", action="store_true", help="Use both neighboring transcript turns.")
    parser.add_argument("--smoke", action="store_true", help="Run two training steps without exporting.")
    args = parser.parse_args()
    if args.smoke and args.resume:
        parser.error("--smoke and --resume cannot be combined.")
    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    rows = [row for row in rows if row.get("label") is not None]
    partitions = {split: [] for split in ("train", "validation", "test")}
    groups, quotes = {}, {}
    for row in rows:
        split = row["split"]
        if row["label"] not in LABELS or split not in partitions:
            parser.error(f"Invalid label or split for {row['id']}")
        quote = " ".join(row["quote"].casefold().split())
        group = (row["source"], row["group"])
        if groups.setdefault(group, split) != split or quotes.setdefault(quote, split) != split:
            parser.error(f"Cross-split group or duplicate utterance at {row['id']}")
        partitions[split].append(row)
    if not partitions["train"]:
        parser.error("Provide at least one reviewed training row.")
    if not args.smoke and (
        any(not partition for partition in partitions.values()) or
        set(row["label"] for row in partitions["train"]) != set(LABELS)
    ):
        parser.error("Provide reviewed examples for all six training classes and nonempty validation and test splits.")

    import numpy as np
    import torch
    from collections import Counter
    from peft import LoraConfig, TaskType, get_peft_model
    from scipy.special import softmax
    from sklearn.metrics import average_precision_score, f1_score
    from commitment_metrics import commitment_targets
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding, Trainer, TrainingArguments, set_seed

    if not torch.cuda.is_available():
        torch.set_num_threads(min(4, torch.get_num_threads()))
    set_seed(42)
    cache = DEFAULT_MODEL.parent / "hf-cache" / "hub"
    revision = "ac19ae4bc51093b31c475665ac872a936d056cc2"
    initial_model = args.initialize_from or BASE_MODEL
    hub_options = {"local_files_only": True} if args.initialize_from else {
        "cache_dir": cache, "revision": revision, "use_safetensors": False,
    }
    tokenizer = AutoTokenizer.from_pretrained(initial_model, **{
        key: value for key, value in hub_options.items() if key != "use_safetensors"
    })
    model = AutoModelForSequenceClassification.from_pretrained(
        initial_model, **hub_options,
        num_labels=len(LABELS), id2label=dict(enumerate(LABELS)),
        label2id={label: index for index, label in enumerate(LABELS)},
    )
    model = get_peft_model(model, LoraConfig(
        task_type=TaskType.SEQ_CLS, target_modules=["Wqkv"], r=8, lora_alpha=16,
        lora_dropout=0.05, modules_to_save=["classifier", "head"],
    ))

    def encode(items):
        first, second = classifier_inputs(items, adjacent_context=args.adjacent_context)
        features = tokenizer(first, second, truncation=True, max_length=160)
        return [
            {**{key: value[i] for key, value in features.items()}, "labels": LABELS.index(row["label"])}
            for i, row in enumerate(items)
        ]

    def metrics(result):
        gold, scores = commitment_targets(result.label_ids, softmax(result.predictions, axis=-1))
        return {
            "macro_f1": f1_score(result.label_ids, np.argmax(result.predictions, axis=-1),
                                 labels=list(range(len(LABELS))), average="macro", zero_division=0),
            "commitment_ap": average_precision_score(gold, scores) if gold.any() and not gold.all() else 0.0,
        }

    support = Counter(row["label"] for row in partitions["train"])
    weights = torch.tensor([min(3., (max(support.values()) / max(1, support[label])) ** .5) for label in LABELS])

    def weighted_loss(outputs, labels, num_items_in_batch=None):
        logits = outputs.logits.float()
        losses = torch.nn.functional.cross_entropy(logits, labels, weight=weights.to(logits.device), reduction="sum")
        return losses / (num_items_in_batch if num_items_in_batch is not None else labels.numel())

    trainer = Trainer(
        model=model, train_dataset=encode(partitions["train"]),
        eval_dataset=None if args.smoke else encode(partitions["validation"]),
        data_collator=DataCollatorWithPadding(tokenizer), compute_metrics=metrics,
        compute_loss_func=weighted_loss if args.balanced else None,
        args=TrainingArguments(
            output_dir=str(args.output.with_name(args.output.name + "-checkpoints")), num_train_epochs=args.epochs,
            max_steps=2 if args.smoke else -1, per_device_train_batch_size=1, per_device_eval_batch_size=4,
            gradient_accumulation_steps=1 if args.smoke else 8,
            learning_rate=1e-4 if args.initialize_from else 2e-4, fp16=torch.cuda.is_available(),
            eval_strategy="no" if args.smoke else "epoch", save_strategy="no" if args.smoke else "epoch",
            load_best_model_at_end=not args.smoke, metric_for_best_model=args.selection_metric,
            report_to="none", save_total_limit=2, seed=42,
        ),
    )
    print("CUDA:", torch.cuda.get_device_name() if torch.cuda.is_available() else "unavailable (CPU)")
    trainer.train(resume_from_checkpoint=args.resume)
    if args.smoke:
        return
    from commitment_metrics import evaluate, show_results, tune_threshold

    validation = trainer.predict(encode(partitions["validation"]))
    validation_probabilities = softmax(validation.predictions, axis=-1)
    threshold = tune_threshold(validation.label_ids, validation_probabilities, beta=args.review_beta)
    validation_report = evaluate(
        {**partitions, "test": partitions["validation"]}, validation.label_ids, validation_probabilities, threshold,
    )["slices"]["all"]
    print(f"Selected validation checkpoint | n={len(validation.label_ids)} | "
          f"macro F1 {validation_report['classification']['macro avg']['f1-score']:.3f} | "
          f"review {validation_report['review_routing']}")
    result = trainer.predict(encode(partitions["test"]))
    probabilities = softmax(result.predictions, axis=-1)
    report = evaluate(partitions, result.label_ids, probabilities, threshold)
    merged = model.merge_and_unload()
    merged.config.promise_review_threshold = threshold
    merged.config.promise_adjacent_context = args.adjacent_context
    merged.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    show_results(report, result.label_ids, probabilities, trainer.state.log_history, args.output)


if __name__ == "__main__":
    main()
