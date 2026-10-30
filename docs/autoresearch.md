# Autoresearch Plugin Guide

`autoresearch` assimilates a Karpathy-style experiment loop into Ouroboros
without putting research-loop-specific behavior into Ouroboros core.

## Install

Install from the reference plugin catalog:

```bash
ouroboros plugin add https://github.com/Q00/ouroboros-plugins --plugin autoresearch
```

If install did not grant required permissions, grant them explicitly:

```bash
ouroboros plugin trust autoresearch \
  --scope filesystem:read \
  --scope filesystem:write
```

For local development:

```bash
git clone https://github.com/Q00/ouroboros-plugins
cd ouroboros-plugins
ouroboros plugin add . --plugin autoresearch
```

## Target Repository Shape

The plugin expects an experiment checkout with three files:

```text
program.md
prepare.py
train.py
```

- `program.md` is the research brief: objective, metric, constraints, and stop
  condition.
- `prepare.py` is fixed support code: data loading, splits, metrics, helpers.
- `train.py` is the default editable experiment file.

By default, the plugin assumes `val_bpb` as the primary metric, lower is
better, and `uv run train.py` as the verification command. You can override
metric, command, and file paths with `prepare` flags.

## Prepare

Check readiness:

```bash
ouroboros auto-research inspect /path/to/research-loop
```

Generate handoff artifacts:

```bash
ouroboros auto-research prepare /path/to/research-loop \
  --goal "Improve validation bits-per-byte with a bounded experiment log" \
  --metric val_bpb \
  --max-experiments 8 \
  --experiment-seconds 300 \
  --train-command "python3 train.py"
```

The plugin writes:

```text
.ouroboros/autoresearch/seed.md
.ouroboros/autoresearch/auto_goal.txt
.ouroboros/autoresearch/handoff.json
```

`seed.md` is only the plugin handoff brief. The saved Ouroboros Seed artifact is
created by the Ouroboros runtime, usually under the user's Ouroboros data
directory, and should not be declared as a repository-local output. Do not add
or require `.ouroboros/autoresearch/seed.yaml`,
`.ouroboros/autoresearch/generated-seed.yaml`, or a top-level
`seed_artifact_path` in the target checkout.

## Run The Full Ouroboros Loop

The plugin only prepares. The full loop starts when you pass the generated goal
to `ouroboros auto`:

```bash
ouroboros auto "$(cat /path/to/research-loop/.ouroboros/autoresearch/auto_goal.txt)"
```

The expected flow is:

```text
plugin prepare
  -> handoff brief and auto goal
  -> auto interview
  -> Seed generation / review / repair
  -> experiment execution
  -> evaluation
  -> reflection or recovery
```

In core implementation terms, `ooo auto` uses phases such as `INTERVIEW`,
`SEED_GENERATION`, `REVIEW`, `REPAIR`, `RUN`, `RALPH_HANDOFF`, and `EVALUATE`.
The runtime routing layer also has `interview`, `execute`, `evaluate`, and
`reflect` stage bindings. So yes: the plugin-prepared handoff should enter the
same interview/execution/evaluate/reflect family as other `ooo auto` runs.

The handoff intentionally includes a concrete experiment plan because `ooo auto`
reviews the generated Seed before execution. In particular, it tells auto to use
experiment 1 as an unmodified baseline and experiments 2-N as concrete
candidate changes inside `train.py`, with explicit non-goals and runtime
context. Without those details, auto can correctly block at Seed QA rather than
running an under-specified research loop.

The generated `seed.md`, `auto_goal.txt`, and `handoff.json` also include an
`autoresearch_contract` JSON object. This is deliberate: actual `ooo auto`
smoke runs showed that prose-only guidance can be treated as examples or schema
instead of concrete Seed values. The contract spells out the repository,
editable files, fixed files, primary metric, experiment budget, candidate
sequence, non-goals, runtime context, metric fallback, ledger columns, and
validity rules so the generated Seed can instantiate them directly. It also
includes `seed_artifact_policy` so `ooo auto` treats the plugin files as input
briefs rather than saved Seed outputs, and `execution_command` so the command
string stays stable while timeout enforcement remains a separate orchestration
budget.

Ownership stays on the plugin side: this repository defines the
`autoresearch_contract` shape and the handoff policy. Ouroboros core should stay
domain-agnostic; it only needs to preserve structured Seed data, including
plugin-owned extra fields, while running the normal interview, execution,
evaluation, ledger, and reflection machinery.

## Demo

Run the deterministic character-language-model demo:

```bash
cd examples/autoresearch-char-lm
python3 train.py
```

Prepare it for Ouroboros:

```bash
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

The demo output includes both `val_bpb` and `best_val_bpb` so the primary metric
is parseable by a strict Seed while remaining easy for humans to read.
