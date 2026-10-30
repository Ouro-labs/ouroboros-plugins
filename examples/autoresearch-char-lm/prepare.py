from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


CORPUS = """
research loops need stable baselines before clever changes
good experiment harnesses record hypotheses metrics and conclusions
validation loss improves when a model captures local structure
small language models need smoothing for rare characters and unseen contexts
bounded automation should preserve a reproducible command trail
failed experiments are useful when the log explains what changed
the best result should include a metric value and a short interpretation
evaluation should compare candidates on the same validation split
""".strip()


@dataclass(frozen=True)
class Dataset:
    train: str
    val: str
    test: str
    alphabet: tuple[str, ...]


def load_dataset() -> Dataset:
    text = CORPUS + "\n"
    train_end = int(len(text) * 0.64)
    val_end = int(len(text) * 0.82)
    return Dataset(
        train=text[:train_end],
        val=text[train_end:val_end],
        test=text[val_end:],
        alphabet=tuple(sorted(set(text))),
    )


def count_ngrams(text: str, order: int) -> dict[str, Counter[str]]:
    if order < 1:
        raise ValueError("order must be >= 1")
    prefix = "\n" * order
    padded = prefix + text
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for index in range(order, len(padded)):
        context = padded[index - order : index]
        current = padded[index]
        counts[context][current] += 1
    return counts


def unigram_counts(text: str) -> Counter[str]:
    return Counter(text)


def bits_per_byte(probabilities: list[float]) -> float:
    if not probabilities:
        raise ValueError("cannot score an empty probability list")
    return -sum(math.log2(max(probability, 1e-12)) for probability in probabilities) / len(
        probabilities
    )


def evaluate_split(probability_fn, split_text: str) -> float:
    previous = "\n\n"
    probabilities: list[float] = []
    for current in split_text:
        probabilities.append(probability_fn(previous, current))
        previous = (previous + current)[-2:]
    return bits_per_byte(probabilities)


def artifact_dir() -> Path:
    path = Path("artifacts")
    path.mkdir(exist_ok=True)
    return path

