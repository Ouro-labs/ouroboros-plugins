"""Command entrypoint for the autoresearch Ouroboros plugin."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_NAME = "autoresearch"
PLUGIN_VERSION = "0.1.0"
UPSTREAM_REPOSITORY = "https://github.com/karpathy/autoresearch"
DEFAULT_PROGRAM_FILE = "program.md"
DEFAULT_TARGET_FILE = "train.py"
DEFAULT_SUPPORT_FILE = "prepare.py"
DEFAULT_METRIC = "val_bpb"
DEFAULT_EXPERIMENT_SECONDS = 300
DEFAULT_MAX_EXPERIMENTS = 8
DEFAULT_TRAIN_COMMAND = "uv run train.py"
ARTIFACT_DIR = Path(".ouroboros") / PLUGIN_NAME


def candidate_sequence(
    *,
    max_experiments: int,
    train_command: str,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = [
        {
            "id": 1,
            "name": "baseline",
            "train_py_change": "none",
            "command": train_command,
            "purpose": "Measure the unmodified starting point before any edits.",
        },
        {
            "id": 2,
            "name": "additive-smoothing",
            "train_py_change": "Tune additive smoothing values in the probability model.",
            "purpose": "Test whether less or more smoothing improves validation bpb.",
        },
        {
            "id": 3,
            "name": "unigram-bigram-interpolation",
            "train_py_change": "Tune interpolation weights between unigram and bigram probabilities.",
            "purpose": "Test a more calibrated local-context mixture.",
        },
        {
            "id": 4,
            "name": "trigram-component",
            "train_py_change": "Add a small trigram component with conservative backoff.",
            "purpose": "Test whether a longer context improves the fixed validation metric.",
        },
        {
            "id": 5,
            "name": "unseen-context-backoff",
            "train_py_change": "Improve fallback behavior for unseen or sparse contexts.",
            "purpose": "Reduce overconfident estimates on sparse n-gram rows.",
        },
        {
            "id": 6,
            "name": "best-combined-configuration",
            "train_py_change": "Combine only changes that individually improved validation bpb.",
            "purpose": "Verify that the best pieces compose without degrading the metric.",
        },
    ]
    if max_experiments <= len(candidates):
        return candidates[:max_experiments]

    for experiment_id in range(len(candidates) + 1, max_experiments + 1):
        candidates.append(
            {
                "id": experiment_id,
                "name": f"targeted-ablation-{experiment_id}",
                "train_py_change": (
                    "Run one focused ablation derived from the experiment ledger; "
                    "edit train.py only."
                ),
                "purpose": "Use the ledger to test the most promising remaining hypothesis.",
            }
        )
    return candidates


def build_autoresearch_contract(
    inspection: RepoInspection,
    *,
    metric: str,
    max_experiments: int,
    experiment_seconds: int,
    train_command: str,
) -> dict[str, object]:
    target_rel = display_path(inspection.target_file, inspection.root)
    program_rel = display_path(inspection.program_file, inspection.root)
    support_rel = display_path(inspection.support_file, inspection.root)
    return {
        "repository": str(inspection.root),
        "program_path": program_rel,
        "handoff_brief_path": str(inspection.root / ARTIFACT_DIR / "seed.md"),
        "editable_files": [target_rel],
        "fixed_files": [program_rel, support_rel],
        "primary_metric": metric,
        "metric_direction": "lower_is_better",
        "experiment_budget": max_experiments,
        "timeout_seconds": experiment_seconds,
        "verification_command": train_command,
        "execution_command": {
            "command": train_command,
            "cwd": str(inspection.root),
            "timeout_seconds": experiment_seconds,
            "timeout_policy": (
                "Apply timeout_seconds in the harness/orchestrator. Do not rewrite "
                "the command string by prefixing shell-specific timeout wrappers."
            ),
        },
        "seed_artifact_policy": {
            "handoff_brief_path": str(inspection.root / ARTIFACT_DIR / "seed.md"),
            "handoff_brief_only": True,
            "saved_seed_path_runtime_owned": True,
            "repo_local_seed_output_required": False,
            "forbidden_repo_local_seed_outputs": [
                str(ARTIFACT_DIR / "seed.yaml"),
                str(ARTIFACT_DIR / "generated-seed.yaml"),
            ],
        },
        "candidate_sequence": candidate_sequence(
            max_experiments=max_experiments,
            train_command=train_command,
        ),
        "non_goals": [
            "Do not change datasets, splits, metric definitions, or support/runtime utility code.",
            "Do not edit prepare.py or program.md during experiment execution.",
            "Do not add external services, network dependencies, credentials, deployment steps, or new frameworks.",
            "Do not optimize for optimizer, learning-rate, neural training-loop, or regularization changes unless the research program already asks for those.",
            "Do not change the metric output contract or fabricate reported metric values.",
        ],
        "runtime_context": {
            "cwd": str(inspection.root),
            "artifacts_local": True,
            "baseline_counts_against_budget": True,
            "final_patch_boundary": target_rel,
        },
        "metric_fallback": {
            "primary": metric,
            "legacy_json_fallback": f"best_{metric}",
        },
        "ledger": {
            "path": str(ARTIFACT_DIR / "experiment-log.md"),
            "columns": [
                "experiment_id",
                "candidate_name",
                "command",
                "changed_files",
                metric,
                "conclusion",
            ],
        },
        "validity_rules": {
            "exit_code": 0,
            "stderr": "no traceback",
            "metric_required": True,
            "baseline_required": True,
            "comparison_rule": "lower primary_metric is better; keep only meaningful improvements over baseline.",
        },
        "verification_plan": {
            "seed_creation": [
                "Inspect the generated Ouroboros Seed artifact; do not run training while authoring the Seed.",
                "Confirm the Seed artifact preserves the plugin-owned autoresearch contract as structured Seed data.",
                "Do not create or require a repository-local Seed YAML such as .ouroboros/autoresearch/seed.yaml or generated-seed.yaml.",
                "Treat the saved Seed artifact path as owned by the Ouroboros runtime, not by the target repository.",
            ],
            "experiment_execution": [
                f"Run every experiment from the repository root with `{train_command}`.",
                f"Enforce a {experiment_seconds}-second timeout for each experiment via the harness/orchestrator, without changing the command string.",
                f"Parse `{metric}` from output, using `best_{metric}` only as the legacy JSON fallback.",
                "Record every attempted experiment in the configured ledger path.",
                "Attempt experiments 2 through the configured budget as concrete train.py candidate changes; a baseline-only rerun or policy-inspection report is not sufficient.",
                f"Report baseline `{metric}`, final best `{metric}`, and whether the best result improved.",
            ],
            "invalid_run_behavior": [
                "Non-zero exit, traceback, timeout, missing metric, changed fixed file, or fabricated metric makes the run invalid.",
                "Invalid runs are recorded as failed and never counted as improvements.",
                "If no candidate improves the baseline, the final report must show all planned candidates were attempted or explicitly rejected with measured evidence.",
            ],
        },
        "conflict_resolution": (
            "This contract is authoritative. If the interview generates optimizer, "
            "learning-rate, neural training-loop, framework, dataset, split, or "
            "metric changes, discard them unless they are explicitly present in "
            "program.md. The probability-model candidate_sequence wins conflicts."
        ),
    }


@dataclass(frozen=True)
class RepoInspection:
    root: Path
    program_file: Path
    target_file: Path
    support_file: Path
    program_exists: bool
    target_exists: bool
    support_exists: bool

    @property
    def ready(self) -> bool:
        return self.program_exists and self.target_exists and self.support_exists

    @property
    def missing(self) -> list[str]:
        missing: list[str] = []
        if not self.program_exists:
            missing.append(display_path(self.program_file, self.root))
        if not self.target_exists:
            missing.append(display_path(self.target_file, self.root))
        if not self.support_exists:
            missing.append(display_path(self.support_file, self.root))
        return missing


def display_path(path: Path, root: Path) -> str:
    """Return a stable repo-relative path when possible."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def repo_member_path(root: Path, raw_path: str, label: str) -> Path:
    """Resolve a user-supplied repo member path without allowing escape."""
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError(f"{label} must be a path relative to the repository root")

    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"{label} must stay inside the repository root"
        ) from exc
    return resolved


def inspect_repo(
    repository_path: str,
    *,
    program_file: str = DEFAULT_PROGRAM_FILE,
    target_file: str = DEFAULT_TARGET_FILE,
    support_file: str = DEFAULT_SUPPORT_FILE,
) -> RepoInspection:
    root = Path(repository_path).expanduser().resolve()
    program = repo_member_path(root, program_file, "--program-file")
    target = repo_member_path(root, target_file, "--target-file")
    support = repo_member_path(root, support_file, "--support-file")
    return RepoInspection(
        root=root,
        program_file=program,
        target_file=target,
        support_file=support,
        program_exists=program.is_file(),
        target_exists=target.is_file(),
        support_exists=support.is_file(),
    )


def read_excerpt(path: Path, limit: int = 4000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[excerpt truncated]"


def write_text_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def fenced_code_block(language: str, content: str) -> list[str]:
    """Return a Markdown code fence that cannot be closed by its content."""
    longest_run = 0
    current_run = 0
    for char in content:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    fence = "`" * max(3, longest_run + 1)
    return [f"{fence}{language}", content.rstrip(), fence]


def build_seed_markdown(
    inspection: RepoInspection,
    *,
    goal: str,
    metric: str,
    max_experiments: int,
    experiment_seconds: int,
    train_command: str,
) -> str:
    program_excerpt = read_excerpt(inspection.program_file)
    target_rel = display_path(inspection.target_file, inspection.root)
    program_rel = display_path(inspection.program_file, inspection.root)
    support_rel = display_path(inspection.support_file, inspection.root)
    generated_at = datetime.now(timezone.utc).isoformat()
    contract = build_autoresearch_contract(
        inspection,
        metric=metric,
        max_experiments=max_experiments,
        experiment_seconds=experiment_seconds,
        train_command=train_command,
    )
    contract_json = json.dumps(contract, indent=2)

    lines = [
        "# Autoresearch Ouroboros Seed",
        "",
        f"generated_at: {generated_at}",
        f"repository: {inspection.root}",
        f"upstream: {UPSTREAM_REPOSITORY}",
        "",
        "## Goal",
        "",
        goal,
        "",
        "## Execution Boundary",
        "",
        "- This Markdown file is the plugin handoff brief. The generated Ouroboros Seed artifact may use the normal Ouroboros YAML/Seed serialization.",
        "- Do not create or require `.ouroboros/autoresearch/seed.yaml`, `.ouroboros/autoresearch/generated-seed.yaml`, or any other repository-local saved Seed YAML.",
        "- The saved Seed artifact path is owned by the Ouroboros runtime. It must not be declared as a target-repository output or top-level `seed_artifact_path` requirement.",
        f"- Edit only `{target_rel}` unless the user explicitly widens scope.",
        f"- Treat `{program_rel}` as the research program and product requirements.",
        f"- Treat `{support_rel}` as fixed data prep and runtime utility code.",
        f"- Run at most {max_experiments} experiments.",
        f"- Keep each experiment bounded to {experiment_seconds} seconds.",
        f"- Use `{metric}` as the primary comparison metric.",
        f"- Prefer changes that improve `{metric}` and preserve a reproducible command trail.",
        "",
        "## Experiment Plan",
        "",
        f"- Experiment 1 is the unmodified baseline run: `{train_command}`. It counts against the {max_experiments}-experiment budget.",
        f"- Experiments 2-{max_experiments} may edit `{target_rel}` only and should test concrete candidate changes or decision branches from `{program_rel}`.",
        f"- After each experiment, record the command, changed files, observed `{metric}`, and conclusion before choosing the next candidate.",
        f"- A candidate succeeds only if its best observed `{metric}` is strictly lower than the baseline by a meaningful margin.",
        f"- If the verification output is JSON, treat a top-level `{metric}` key as primary; `best_{metric}` may be used as a fallback for legacy scripts.",
        "- During execution, a baseline-only rerun or policy-inspection report is not sufficient; run or explicitly reject each planned candidate with measured evidence.",
        "",
        "## Authoritative Autoresearch Contract",
        "",
        "Preserve these values as concrete Seed data: use native Seed fields where they fit, and plugin-owned extra fields for autoresearch-specific values. Do not convert them into examples, ontology schema, personas, or optional guidance.",
        "",
        *fenced_code_block("json", contract_json),
        "",
        "## Non-Goals",
        "",
        "- Do not change datasets, data splits, metric definitions, or support/runtime utility code.",
        "- Do not add external services, network dependencies, credentials, deployment steps, or new frameworks.",
        "- Do not optimize for any metric other than the primary comparison metric unless the user explicitly widens scope.",
        "",
        "## Runtime Context",
        "",
        f"- Work from repository root `{inspection.root}`.",
        f"- Run verification from that root with `{train_command}`.",
        f"- Apply the {experiment_seconds}-second experiment timeout as a harness/orchestrator budget; keep the command string exactly `{train_command}`.",
        f"- Keep `{support_rel}` fixed and use it only as support/evaluation infrastructure.",
        f"- Keep generated experiment artifacts local to the repository unless the user asks for export.",
        "",
        "## Verification Command",
        "",
        *fenced_code_block("bash", train_command),
        "",
        "## Acceptance Criteria",
        "",
        f"- The final result reports the best observed `{metric}` and the baseline value if available.",
        f"- Every planned candidate experiment records command, changed files, observed `{metric}`, and conclusion.",
        f"- The final report compares baseline `{metric}` against final best `{metric}` and states whether the best result improved.",
        "- A baseline-only rerun or validation-only report fails the research execution.",
        f"- The final patch is limited to `{target_rel}` unless scope was widened in the ledger.",
        f"- Experiment 1 is an unmodified baseline run and any improvement is compared against that baseline.",
        f"- The final verification command exits 0 and emits parseable `{metric}` or `best_{metric}` output.",
        "- The run stops when the experiment budget is exhausted or no promising next edit remains.",
        "",
        "## Program Excerpt",
        "",
        *fenced_code_block("markdown", program_excerpt),
        "",
    ]
    return "\n".join(lines)


def build_auto_goal(
    inspection: RepoInspection,
    *,
    goal: str,
    metric: str,
    max_experiments: int,
    experiment_seconds: int,
    train_command: str,
    seed_path: Path,
) -> str:
    target_rel = display_path(inspection.target_file, inspection.root)
    program_rel = display_path(inspection.program_file, inspection.root)
    support_rel = display_path(inspection.support_file, inspection.root)
    contract = build_autoresearch_contract(
        inspection,
        metric=metric,
        max_experiments=max_experiments,
        experiment_seconds=experiment_seconds,
        train_command=train_command,
    )
    contract_json = json.dumps(contract, separators=(",", ":"))
    return "\n".join(
        [
            goal,
            "",
            "Use the prepared autoresearch handoff brief at:",
            str(seed_path),
            "",
            "Concrete constraints for the generated Ouroboros Seed:",
            f"- Work in repository: {inspection.root}",
            f"- Treat `{program_rel}` as the research program/instructions.",
            f"- Edit only `{target_rel}` unless explicitly widening scope in the ledger.",
            f"- Treat `{support_rel}` as fixed data prep/runtime utility code.",
            f"- Run at most {max_experiments} experiments.",
            f"- Keep each experiment bounded to {experiment_seconds} seconds.",
            f"- Use `{metric}` as the primary metric; lower is better.",
            f"- Verification command: `{train_command}`.",
            f"- Apply the {experiment_seconds}-second timeout as a separate execution budget; do not rewrite the command into a shell-specific timeout wrapper.",
            f"- Experiment 1 must be an unmodified baseline run and counts against the {max_experiments}-experiment budget.",
            f"- Experiments 2-{max_experiments} must be concrete candidate changes or decision branches inside `{target_rel}`.",
            f"- Include top-level non_goals: do not change datasets, splits, metric code, `{support_rel}`, external services, credentials, deployment, or frameworks.",
            f"- Include runtime_context: run from `{inspection.root}` with `{train_command}` and keep artifacts local.",
            "- Include seed_artifact_policy: `seed.md` is only the plugin handoff brief, saved Seed paths are runtime-owned, and repo-local Seed YAML outputs are not required.",
            "- Do not declare top-level `seed_artifact_path`, `.ouroboros/autoresearch/seed.yaml`, or `.ouroboros/autoresearch/generated-seed.yaml` as required target-repository outputs.",
            f"- Accept parseable `{metric}` as primary; if output is JSON and only `best_{metric}` exists, treat that as a legacy fallback.",
            "- Do not run training during Seed creation; only prepare the bounded plan unless execution is explicitly requested.",
            "- The generated Ouroboros Seed artifact may use the normal Ouroboros YAML/Seed serialization. `seed.md` is the plugin handoff brief, not a required output format for auto's saved Seed.",
            "",
            "Authoritative autoresearch_contract JSON follows. The generated Seed must preserve these as concrete Seed data, using plugin-owned extra fields for autoresearch-specific values, not merely schema examples:",
            "",
            *fenced_code_block("json", contract_json),
            "",
        ]
    )


def write_handoff(
    inspection: RepoInspection,
    *,
    goal: str,
    metric: str,
    max_experiments: int,
    experiment_seconds: int,
    train_command: str,
) -> dict:
    if not inspection.ready:
        raise ValueError("repository is missing required autoresearch files")

    out_dir = inspection.root / ARTIFACT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_path = out_dir / "seed.md"
    auto_goal_path = out_dir / "auto_goal.txt"
    handoff_path = out_dir / "handoff.json"

    seed = build_seed_markdown(
        inspection,
        goal=goal,
        metric=metric,
        max_experiments=max_experiments,
        experiment_seconds=experiment_seconds,
        train_command=train_command,
    )
    write_text_atomic(seed_path, seed)
    auto_goal = build_auto_goal(
        inspection,
        goal=goal,
        metric=metric,
        max_experiments=max_experiments,
        experiment_seconds=experiment_seconds,
        train_command=train_command,
        seed_path=seed_path,
    )
    write_text_atomic(auto_goal_path, auto_goal)

    handoff = {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "status": "prepared",
        "repository": str(inspection.root),
        "seed_path": str(seed_path),
        "auto_goal_path": str(auto_goal_path),
        "handoff_path": str(handoff_path),
        "upstream": UPSTREAM_REPOSITORY,
        "ooo_auto": {
            "recommended_command": (
                f"ouroboros auto \"$(cat {shlex.quote(str(auto_goal_path))})\""
            ),
            "goal": goal,
            "metric": metric,
            "max_experiments": max_experiments,
            "experiment_seconds": experiment_seconds,
            "train_command": train_command,
            "editable_files": [display_path(inspection.target_file, inspection.root)],
            "autoresearch_contract": build_autoresearch_contract(
                inspection,
                metric=metric,
                max_experiments=max_experiments,
                experiment_seconds=experiment_seconds,
                train_command=train_command,
            ),
        },
    }
    write_text_atomic(handoff_path, json.dumps(handoff, indent=2) + "\n")
    return handoff


def inspection_payload(inspection: RepoInspection) -> dict:
    return {
        "plugin": PLUGIN_NAME,
        "status": "ready" if inspection.ready else "missing_prerequisites",
        "repository": str(inspection.root),
        "required_files": {
            display_path(inspection.program_file, inspection.root): inspection.program_exists,
            display_path(inspection.target_file, inspection.root): inspection.target_exists,
            display_path(inspection.support_file, inspection.root): inspection.support_exists,
        },
        "missing": inspection.missing,
        "ooo_auto_ready": inspection.ready,
    }


def positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoresearch")
    parser.add_argument("command", choices=["inspect", "prepare"])
    parser.add_argument("repository_path")
    parser.add_argument("--program-file", default=DEFAULT_PROGRAM_FILE)
    parser.add_argument("--target-file", default=DEFAULT_TARGET_FILE)
    parser.add_argument("--support-file", default=DEFAULT_SUPPORT_FILE)
    parser.add_argument("--goal", default="")
    parser.add_argument("--metric", default=DEFAULT_METRIC)
    parser.add_argument("--max-experiments", type=positive_int, default=DEFAULT_MAX_EXPERIMENTS)
    parser.add_argument("--experiment-seconds", type=positive_int, default=DEFAULT_EXPERIMENT_SECONDS)
    parser.add_argument("--train-command", default=DEFAULT_TRAIN_COMMAND)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        inspection = inspect_repo(
            args.repository_path,
            program_file=args.program_file,
            target_file=args.target_file,
            support_file=args.support_file,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.command == "inspect":
        sys.stdout.write(json.dumps(inspection_payload(inspection), indent=2) + "\n")
        return 0 if inspection.ready else 1

    if not args.goal.strip():
        parser.error("prepare requires --goal")
    if not inspection.ready:
        missing = ", ".join(inspection.missing)
        sys.stderr.write(f"autoresearch: missing required file(s): {missing}\n")
        return 1

    handoff = write_handoff(
        inspection,
        goal=args.goal.strip(),
        metric=args.metric,
        max_experiments=args.max_experiments,
        experiment_seconds=args.experiment_seconds,
        train_command=args.train_command,
    )
    sys.stdout.write(json.dumps(handoff, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
