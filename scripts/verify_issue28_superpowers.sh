#!/usr/bin/env bash
# Verify local contracts and real plugin dispatch. L3 inference is opt-in.
# Overrides: SOURCE_REPO, WORKTREE (existing checkout), EVIDENCE_ROOT,
# RUN_L3A=1, L3A_INTERVIEW_ROUNDS=6, L3A_TIMEOUT_SECONDS=300.
set -euo pipefail
SOURCE_REPO="${SOURCE_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WORKTREE="${WORKTREE:-$SOURCE_REPO}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/superpowers-verify.XXXXXXXX")}"
RUN_L3A="${RUN_L3A:-0}"
export EVIDENCE_ROOT WORKTREE
mkdir -p "$EVIDENCE_ROOT/logs"
# Never fetch, reset, delete, or replace a caller's checkout.
git -C "$WORKTREE" rev-parse --show-toplevel >/dev/null
cd "$WORKTREE"
exec > >(tee "$EVIDENCE_ROOT/verify.log") 2>&1
trap 'rc=$?; printf "exit=%s\n" "$rc" > "$EVIDENCE_ROOT/exit-status.txt"' EXIT
run_logged() {
  local logfile="$1"
  shift
  printf '%q ' "$@" > "$logfile.cmd"
  printf '\n' >> "$logfile.cmd"
  "$@" 2>&1 | tee "$logfile"
}
printf 'commit=%s\npython=%s\n' "$(git rev-parse HEAD)" "$(command -v python3)" > "$EVIDENCE_ROOT/environment.txt"
run_logged "$EVIDENCE_ROOT/logs/validate.log" python3 scripts/validate_contract.py
run_logged "$EVIDENCE_ROOT/logs/tests.log" python3 -m unittest tests.test_superpowers_plugin tests.test_validator tests.test_autoresearch_plugin -v
L1_RESULT='BLOCKED: ouroboros CLI unavailable'
L2_RESULT='NOT RUN'
L3_RESULT='NOT RUN: inference disabled; --skip-run still invokes models'
PLUGIN_HOME="$WORKTREE/plugins/superpowers"
export TRUST_AUDIT_REQUIRED=0
if command -v ouroboros >/dev/null 2>&1; then
  ouroboros --version >> "$EVIDENCE_ROOT/environment.txt"
  run_logged "$EVIDENCE_ROOT/plugin-add.log" ouroboros plugin add . --plugin superpowers \
    --plugin-home-root "$EVIDENCE_ROOT/plugin-home" --trust-root "$EVIDENCE_ROOT/trust-root" \
    --cache-root "$EVIDENCE_ROOT/cache" --lockfile "$EVIDENCE_ROOT/plugins.lock" \
    --catalog-state "$EVIDENCE_ROOT/plugin-catalogs.json"
  run_logged "$EVIDENCE_ROOT/inspect-before.log" ouroboros plugin inspect superpowers --lockfile "$EVIDENCE_ROOT/plugins.lock" --trust-root "$EVIDENCE_ROOT/trust-root"
  run_logged "$EVIDENCE_ROOT/plugin-trust.log" ouroboros plugin trust superpowers \
    --scope filesystem:read --scope filesystem:write --lockfile "$EVIDENCE_ROOT/plugins.lock" \
    --trust-root "$EVIDENCE_ROOT/trust-root" --audit-log "$EVIDENCE_ROOT/trust-audit.jsonl" --granted-by user:issue28-verify
  TRUST_AUDIT_REQUIRED=1
  run_logged "$EVIDENCE_ROOT/inspect-after.log" ouroboros plugin inspect superpowers --lockfile "$EVIDENCE_ROOT/plugins.lock" --trust-root "$EVIDENCE_ROOT/trust-root"
  run_logged "$EVIDENCE_ROOT/plugin-list.log" ouroboros plugin list --lockfile "$EVIDENCE_ROOT/plugins.lock" --trust-root "$EVIDENCE_ROOT/trust-root" --json
  PLUGIN_HOME="$EVIDENCE_ROOT/plugin-home/superpowers"
  export OUROBOROS_PLUGIN_LOCKFILE="$EVIDENCE_ROOT/plugins.lock"
  export OUROBOROS_PLUGIN_TRUST_ROOT="$EVIDENCE_ROOT/trust-root"
  export OUROBOROS_PLUGIN_OUTPUT_DIR="$EVIDENCE_ROOT/dispatch-output"
  for attempt in 1 2; do
    ouroboros superpowers test-driven-development --goal 'Add retry behavior' --input 'network client' > "$EVIDENCE_ROOT/dispatch-$attempt.json"
  done
  L1_RESULT='PASS: install, inspect, trust, list and repeated firewall dispatch'
  L2_RESULT='PASS: installed module and dispatcher evidence checked'
fi
export PLUGIN_HOME
PYTHONPATH="$PLUGIN_HOME" python3 -m superpowers_ouroboros --output-dir "$EVIDENCE_ROOT/direct-output" test-driven-development --goal 'Add retry behavior' --input 'network client' > "$EVIDENCE_ROOT/command-output.json"
PYTHONPATH="$PLUGIN_HOME" python3 -m superpowers_ouroboros --output-dir "$EVIDENCE_ROOT/finish-output" finishing-a-development-branch --input 'feature/example' > "$EVIDENCE_ROOT/finishing-output.json"
python3 - <<'PY'
import json, os
from pathlib import Path
from jsonschema import Draft202012Validator
root = Path(os.environ['EVIDENCE_ROOT'])
validator = Draft202012Validator(json.loads(Path('schemas/0.1/audit-event.schema.json').read_text()))
required = {'invocation.json', 'provenance.json', 'handoff.md', 'seed.md', 'evidence.json', 'audit.jsonl'}
outputs = [root / 'command-output.json', root / 'finishing-output.json', *sorted(root.glob('dispatch-*.json'))]
for output in outputs:
    payload = json.loads(output.read_text())
    run = Path(payload['run_dir'])
    assert required <= {p.name for p in run.iterdir()}, run
    invocation = json.loads((run / 'invocation.json').read_text())
    assert not invocation.get('goal') and not invocation.get('input')
    provenance = json.loads((run / 'provenance.json').read_text())
    assert {'upstream_repo','upstream_version','upstream_commit','upstream_license','upstream_skill'} <= provenance.keys()
    events = [json.loads(line) for line in (run / 'audit.jsonl').read_text().splitlines() if line.strip()]
    assert [e['event_type'] for e in events] == ['plugin.invoked','plugin.permission_used','plugin.completed']
    for event in events:
        validator.validate(event)
    if output.name == 'finishing-output.json':
        # This checks the handoff instruction. The adapter does not execute git.
        assert 'Destructive upstream actions are report-only' in (run / 'handoff.md').read_text()
    print('PASS artifacts, audit, provenance, bounded invocation:', output.name)
trust = root / 'trust-audit.jsonl'
if os.environ['TRUST_AUDIT_REQUIRED'] == '1':
    assert trust.is_file(), 'missing trust audit after successful plugin trust'
if trust.exists():
    events = [json.loads(line) for line in trust.read_text().splitlines() if line.strip()]
    assert events, 'empty trust audit is not evidence'
    payloads = [event.get('payload', event) for event in events]
    assert any(event.get('event_type') == 'plugin.trusted' for event in payloads)
    for event in payloads:
        validator.validate(event)
    print('PASS nonempty trust audit')
PY
if command -v ouroboros >/dev/null 2>&1; then
  run_logged "$EVIDENCE_ROOT/l3-preflight.log" ouroboros auto --help
fi
if [ "$RUN_L3A" = 1 ]; then
  command -v ouroboros >/dev/null 2>&1 || { echo 'L3 BLOCKED: CLI missing'; exit 2; }
  command -v timeout >/dev/null 2>&1 || { echo 'L3 BLOCKED: timeout utility missing'; exit 2; }
  HANDOFF_PATH="$(python3 -c 'import json,os; print(json.load(open(os.environ["EVIDENCE_ROOT"]+"/command-output.json"))["handoff_path"])')"
  # Opt-in invokes models even though experiment execution is skipped.
  run_logged "$EVIDENCE_ROOT/l3-auto.log" timeout "${L3A_TIMEOUT_SECONDS:-300}" ouroboros auto --skip-run --max-interview-rounds "${L3A_INTERVIEW_ROUNDS:-6}" --runtime codex "Use the prepared handoff at $HANDOFF_PATH to prepare a Seed. actors: operator; inputs: handoff and seed.md; outputs: Seed and verification plan; constraints: no network or shell; non_goals: merge, push, branch deletion, PR mutation; failure_modes: retry storm, missing audit; runtime_context: codex."
  L3_RESULT='COMMAND COMPLETED: inspect runtime Seed and grade separately; no grade inferred'
fi
cat > "$EVIDENCE_ROOT/summary.md" <<EOF
# Superpowers verification

Commit $(git rev-parse HEAD).

| Level | Outcome |
|---|---|
| L0 contracts and tests | PASS |
| L1 lifecycle and trust | $L1_RESULT |
| L2 artifacts and dispatcher | $L2_RESULT |
| L2b report-only handoff instruction | PASS; instruction only |
| L3 auto Seed generation | $L3_RESULT |

No checkout was fetched, replaced or removed. No model-quality or training result
is inferred from artifact creation. This script preserves evidence on failure.
EOF
cat "$EVIDENCE_ROOT/summary.md"
[ "$L1_RESULT" != 'BLOCKED: ouroboros CLI unavailable' ]
