from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from prepare import (
    artifact_dir,
    count_ngrams,
    evaluate_split,
    load_dataset,
    unigram_counts,
)


@dataclass(frozen=True)
class Candidate:
    name: str
    alpha: float
    bigram_weight: float
    note: str


# Autoresearch should edit this candidate set or the probability model below.
CANDIDATES = [
    Candidate("unigram-heavy", alpha=1.0, bigram_weight=0.20, note="mostly unigram backoff"),
    Candidate("balanced-bigram", alpha=0.8, bigram_weight=0.55, note="simple interpolated bigram"),
    Candidate("bigram-heavy", alpha=0.5, bigram_weight=0.80, note="strong local context"),
]


def build_probability_fn(candidate: Candidate):
    dataset = load_dataset()
    alphabet = dataset.alphabet
    vocab_size = len(alphabet)
    uni_counts = unigram_counts(dataset.train)
    bi_counts = count_ngrams(dataset.train, order=1)
    total_uni = sum(uni_counts.values())

    def unigram_probability(current: str) -> float:
        return (uni_counts[current] + candidate.alpha) / (
            total_uni + candidate.alpha * vocab_size
        )

    def bigram_probability(previous_two: str, current: str) -> float:
        context = previous_two[-1:]
        row = bi_counts.get(context, {})
        row_total = sum(row.values())
        return (row.get(current, 0) + candidate.alpha) / (
            row_total + candidate.alpha * vocab_size
        )

    def probability(previous_two: str, current: str) -> float:
        unigram = unigram_probability(current)
        bigram = bigram_probability(previous_two, current)
        return (candidate.bigram_weight * bigram) + (
            (1.0 - candidate.bigram_weight) * unigram
        )

    return probability


def run_experiments() -> list[dict]:
    dataset = load_dataset()
    rows: list[dict] = []
    for index, candidate in enumerate(CANDIDATES, start=1):
        probability = build_probability_fn(candidate)
        val_bpb = evaluate_split(probability, dataset.val)
        test_bpb = evaluate_split(probability, dataset.test)
        rows.append(
            {
                "experiment": index,
                "candidate": asdict(candidate),
                "metric": "val_bpb",
                "val_bpb": round(val_bpb, 6),
                "test_bpb": round(test_bpb, 6),
                "lower_is_better": True,
            }
        )
    return rows


def main() -> None:
    rows = run_experiments()
    out_path = artifact_dir() / "experiments.jsonl"
    out_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    best = min(rows, key=lambda row: row["val_bpb"])
    print(
        json.dumps(
            {
                "metric": "val_bpb",
                "val_bpb": best["val_bpb"],
                "best_val_bpb": best["val_bpb"],
                "best_test_bpb": best["test_bpb"],
                "best_candidate": best["candidate"]["name"],
                "experiments": len(rows),
                "log_path": str(out_path),
                "lower_is_better": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
