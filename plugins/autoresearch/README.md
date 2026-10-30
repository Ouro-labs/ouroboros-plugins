# autoresearch

Ouroboros plugin for connecting Karpathy-style autoresearch loops to
`ooo auto`.

The upstream workflow in
[`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) treats
`program.md` as the research brief, keeps `prepare.py` fixed, repeatedly edits
`train.py`, runs bounded experiments with `uv run train.py`, and compares
results with `val_bpb`. This plugin keeps that workflow out of Ouroboros core
while making the handoff explicit and auditable.

## Install

Install from the published plugin catalog:

```bash
ouroboros plugin add https://github.com/Q00/ouroboros-plugins --plugin autoresearch
```

`autoresearch` needs `filesystem:read` and `filesystem:write` so it can inspect
the target experiment checkout and write `.ouroboros/autoresearch/*` handoff
artifacts. Current Ouroboros versions prompt to grant these required
non-destructive scopes during `plugin add`. If you decline, grant them later:

```bash
ouroboros plugin trust autoresearch \
  --scope filesystem:read \
  --scope filesystem:write
```

For plugin development, install from this repository root:

```bash
ouroboros plugin add . --plugin autoresearch
```

## Commands

```bash
PYTHONPATH=plugins/autoresearch python3 -m ouroboros_autoresearch inspect /path/to/autoresearch
PYTHONPATH=plugins/autoresearch python3 -m ouroboros_autoresearch prepare /path/to/autoresearch \
  --goal "Improve validation bpb within the standard five-minute loop"
```

When an Ouroboros command dispatcher is available, the manifest exposes the
same workflow as:

```bash
ooo auto-research inspect /path/to/autoresearch
ooo auto-research prepare /path/to/autoresearch --goal "Improve validation bpb"
```

`inspect` is read-only. It checks that the target checkout has the expected
`program.md`, `prepare.py`, and `train.py` files.

`prepare` writes:

- `.ouroboros/autoresearch/seed.md`
- `.ouroboros/autoresearch/auto_goal.txt`
- `.ouroboros/autoresearch/handoff.json`

The generated handoff includes the recommended
`ouroboros auto "$(cat .../auto_goal.txt)"` command, the editable file
boundary, experiment budget, metric, and verification command. `--attach-source`
is intentionally not used here because current Ouroboros uses it only for
attaching an already-started run handle, not for loading a Seed or brief file.
`seed.md` is a handoff brief for `ooo auto`, not the saved Ouroboros Seed. The
saved Seed path is owned by the Ouroboros runtime, so the plugin does not create
or require `.ouroboros/autoresearch/seed.yaml`,
`.ouroboros/autoresearch/generated-seed.yaml`, or a top-level
`seed_artifact_path`.

The autoresearch contract is plugin-owned. Ouroboros core should not import an
`autoresearch` module, special-case research loops, or normalize this contract
with domain-specific code. The plugin writes the authoritative
`autoresearch_contract` into the handoff; `ooo auto` treats it as structured
Seed data and preserves plugin-specific values through normal Seed extra-field
serialization.

The optional layout flags (`--program-file`, `--target-file`, and
`--support-file`) must be repository-relative paths. Absolute paths and `..`
escapes are rejected so the handoff remains bounded to the inspected checkout.

## End-to-End Workflow

An autoresearch-compatible target checkout looks like:

```text
program.md   # research brief and metric definition
prepare.py   # fixed data prep / evaluation helpers
train.py     # editable experiment implementation
```

Prepare the handoff:

```bash
ouroboros auto-research inspect /path/to/research-loop
ouroboros auto-research prepare /path/to/research-loop \
  --goal "Improve validation bits-per-byte with a bounded experiment log" \
  --max-experiments 8 \
  --experiment-seconds 300 \
  --train-command "python3 train.py"
```

Then hand the generated goal to the normal Ouroboros auto pipeline:

```bash
ouroboros auto "$(cat /path/to/research-loop/.ouroboros/autoresearch/auto_goal.txt)"
```

Use `--skip-run` if you only want `ouroboros auto` to converge and review the
Seed. Use `--complete-product` when you want the post-run evaluation/Ralph loop
to keep iterating within budget.

The key boundary is:

```text
autoresearch plugin prepare
  -> handoff brief + auto goal
  -> ouroboros auto
  -> interview -> seed review/repair -> run -> evaluate -> reflect/recover
```

The plugin does not execute training or replace `ooo auto`; it makes the
research loop legible to `ooo auto`.

`prepare` includes the Seed details that `ooo auto` needs before execution:
experiment 1 as the unmodified baseline, experiments 2-N as concrete candidate
changes inside `train.py`, explicit non-goals, runtime context, metric parsing,
and the verification command. If those details are missing, `ooo auto` may
correctly stop in Seed QA rather than running an under-specified experiment.
It also writes an `autoresearch_contract` JSON block into `seed.md`,
`auto_goal.txt`, and `handoff.json` so the generated Seed has exact values for
the repository, editable files, fixed files, metric, experiment budget,
candidate sequence, ledger, validity rules, runtime-owned Seed artifact policy,
and the separate experiment timeout budget.

See [`docs/autoresearch.md`](../../docs/autoresearch.md) for a fuller guide and
[`examples/autoresearch-char-lm`](../../examples/autoresearch-char-lm) for a
small deterministic experiment that behaves like a real autoresearch loop.

## Product Boundary

This plugin owns the autoresearch-specific workflow:

- Mapping `program.md` and `train.py` into an Ouroboros Seed
- Preserving the experiment budget and metric as auditable constraints
- Handing the prepared Seed to `ooo auto`
- Owning the `autoresearch_contract` schema, policy, and handoff wording

`ooo auto` remains responsible for interview, Seed execution, ledger, and
runtime orchestration. Ouroboros core remains domain-agnostic and does not need
to understand neural-network research loops directly.
