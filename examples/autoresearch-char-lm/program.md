# Research Program: Character Language-Model Experiment

Improve validation bits-per-byte (`val_bpb`) for a small character language
model on a fixed corpus of research-operation notes.

## Objective

Find a simple, reproducible model configuration that lowers `val_bpb` without
changing the dataset, split, or metric code.

## Constraints

- Edit only `train.py`.
- Treat `prepare.py` as fixed data and evaluation infrastructure.
- Keep the experiment deterministic.
- Keep runtime under one minute on a laptop.
- Record each attempted configuration in `artifacts/experiments.jsonl`.
- Optimize `val_bpb`; lower is better.

## Suggested Research Directions

- Tune additive smoothing.
- Tune interpolation between unigram and bigram probabilities.
- Add a trigram component if it improves validation loss.
- Improve backoff behavior for unseen contexts.
- Keep the model interpretable enough to explain in the final report.

## Required Final Report

Report:

- baseline best `val_bpb`
- final best `val_bpb`
- changed configurations or model logic
- number of experiments run
- why the final configuration should generalize better than the baseline

