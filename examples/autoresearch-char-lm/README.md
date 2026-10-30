# Autoresearch Character LM Demo

This is a deterministic, dependency-free autoresearch target. It is small
enough to run in CI or a laptop shell, but it has the shape of a real
experiment:

- fixed dataset and metrics in `prepare.py`
- a research brief in `program.md`
- editable experiment code in `train.py`
- multiple candidate configurations
- JSONL experiment log
- `val_bpb` as the primary metric, lower is better

## Baseline

```bash
python3 train.py
```

The script prints the best validation result and writes
`artifacts/experiments.jsonl`.

Current baseline output:

```json
{
  "best_candidate": "bigram-heavy",
  "best_test_bpb": 3.815081,
  "best_val_bpb": 3.791757,
  "experiments": 3,
  "log_path": "artifacts/experiments.jsonl",
  "lower_is_better": true,
  "metric": "val_bpb",
  "val_bpb": 3.791757
}
```

## Prepare For Ouroboros

After installing and trusting the `autoresearch` plugin:

```bash
ouroboros auto-research inspect "$PWD"
ouroboros auto-research prepare "$PWD" \
  --goal "Improve val_bpb for the character language-model experiment while preserving the fixed dataset and experiment log." \
  --max-experiments 6 \
  --experiment-seconds 60 \
  --train-command "python3 train.py"
```

Then run:

```bash
ouroboros auto "$(cat .ouroboros/autoresearch/auto_goal.txt)"
```

The intended auto edit boundary is `train.py`. Good improvements include adding
trigram interpolation, tuning the candidate grid, or improving fallback
behavior while keeping `prepare.py` fixed.
