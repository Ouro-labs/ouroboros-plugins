from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
PLUGIN_PATH = REPO / "plugins" / "superpowers"
MANIFEST = json.loads((PLUGIN_PATH / "ouroboros.plugin.json").read_text())
SKILLS_ROOT = PLUGIN_PATH / "vendor" / "superpowers" / "skills"
AUDIT_SCHEMA = json.loads((REPO / "schemas" / "0.1" / "audit-event.schema.json").read_text())
AUDIT_VALIDATOR = Draft202012Validator(AUDIT_SCHEMA)

EXPECTED_SKILLS = {
    "brainstorming",
    "dispatching-parallel-agents",
    "executing-plans",
    "finishing-a-development-branch",
    "receiving-code-review",
    "requesting-code-review",
    "subagent-driven-development",
    "systematic-debugging",
    "test-driven-development",
    "using-git-worktrees",
    "using-superpowers",
    "verification-before-completion",
    "writing-plans",
    "writing-skills",
}


class SuperpowersPluginTests(unittest.TestCase):
    def _run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "superpowers_ouroboros", *args],
            cwd=cwd or REPO,
            env={**os.environ, "PYTHONPATH": str(PLUGIN_PATH), **(env or {})},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_manifest_exposes_every_upstream_skill_command(self):
        vendored = {p.parent.name for p in SKILLS_ROOT.glob("*/SKILL.md")}
        self.assertEqual(vendored, EXPECTED_SKILLS)
        commands = {(c["namespace"], c["name"]) for c in MANIFEST["commands"]}
        for skill in vendored:
            self.assertIn(("superpowers", skill), commands)
        self.assertIn(("superpowers", "list"), commands)
        self.assertIn(("superpowers", "inspect"), commands)
        self.assertIn(("superpowers", "prepare-handoff"), commands)
        self.assertIn(("superpowers", "run"), commands)

    def test_list_writes_skill_index_with_upstream_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            proc = self._run("--output-dir", str(Path(td) / ".omx" / "superpowers"), "list")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["skill_count"], len(EXPECTED_SKILLS))
        self.assertEqual({s["name"] for s in payload["skills"]}, EXPECTED_SKILLS)
        self.assertEqual(payload["skills"][0]["upstream_repo"], "https://github.com/obra/superpowers")
        self.assertEqual(payload["skills"][0]["upstream_version"], "v5.1.0")
        self.assertEqual(payload["skills"][0]["upstream_commit"], "f2cbfbefebbfef77321e4c9abc9e949826bea9d7")

    def test_risk_classification_keeps_destructive_workflows_non_destructive_in_v0(self):
        commands = {c["name"]: c for c in MANIFEST["commands"] if c["namespace"] == "superpowers"}
        self.assertEqual(commands["inspect"]["risk"], "read_only")
        self.assertEqual(commands["list"]["risk"], "write")
        self.assertEqual(commands["using-superpowers"]["risk"], "write")
        self.assertEqual(commands["requesting-code-review"]["risk"], "write")
        self.assertEqual(commands["verification-before-completion"]["risk"], "write")
        self.assertEqual(commands["finishing-a-development-branch"]["risk"], "write")
        self.assertTrue(all(c["risk"] != "destructive" for c in commands.values()))

    def test_prepare_handoff_writes_seed_audit_and_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / ".omx" / "superpowers"
            proc = self._run(
                "--output-dir",
                str(out),
                "prepare-handoff",
                "test-driven-development",
                "--goal",
                "Add retry behavior",
                "--input",
                "network client",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            run_dir = Path(payload["run_dir"])
            for name in ["invocation.json", "provenance.json", "handoff.md", "seed.md", "evidence.json", "audit.jsonl"]:
                self.assertTrue((run_dir / name).is_file(), name)
            invocation = json.loads((run_dir / "invocation.json").read_text())
            provenance = json.loads((run_dir / "provenance.json").read_text())
            evidence = json.loads((run_dir / "evidence.json").read_text())
            handoff = (run_dir / "handoff.md").read_text()
            audit_lines = (run_dir / "audit.jsonl").read_text().splitlines()

        self.assertEqual(invocation["upstream_skill"], "test-driven-development")
        self.assertNotIn("goal", invocation)
        self.assertNotIn("input", invocation)
        self.assertEqual(invocation["arguments"]["goal_length"], len("Add retry behavior"))
        self.assertEqual(invocation["arguments"]["input_length"], len("network client"))
        self.assertEqual(
            {permission["scope"] for permission in invocation["used_permissions"]},
            {"filesystem:read", "filesystem:write"},
        )
        self.assertEqual(provenance["upstream_repo"], "https://github.com/obra/superpowers")
        self.assertEqual(provenance["upstream_skill"], "test-driven-development")
        self.assertIn("failing-test output", handoff)
        self.assertIn("Seed-preparation handoff", handoff)
        self.assertEqual(evidence["status"], "prepared")
        audit_events = [json.loads(line) for line in audit_lines]
        for event in audit_events:
            self.assertEqual(list(AUDIT_VALIDATOR.iter_errors(event)), [])
        self.assertEqual(
            [event["event_type"] for event in audit_events],
            ["plugin.invoked", "plugin.permission_used", "plugin.completed"],
        )
        permission_event = [event for event in audit_events if event["event_type"] == "plugin.permission_used"][0]
        self.assertEqual(set(permission_event["permissions_used"]), {"filesystem:read", "filesystem:write"})

    def test_skill_command_alias_prepares_handoff_and_excludes_destructive_actions(self):
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(
                "--output-dir",
                str(Path(td) / ".omx" / "superpowers"),
                "finishing-a-development-branch",
                "--input",
                "feature/todo",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            handoff = Path(payload["handoff_path"]).read_text()
            invocation = json.loads(Path(payload["invocation_path"]).read_text())
        self.assertIn("Destructive upstream actions are report-only in v0", handoff)
        self.assertIn("no merge, push, branch deletion, discard, or PR mutation", handoff)
        self.assertTrue(all(permission["risk"] != "destructive" for permission in invocation["planned_permissions"]))

    def test_dispatch_style_output_dir_after_subcommand_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "dispatch-output"
            proc = self._run(
                "test-driven-development",
                "--output-dir",
                str(out),
                "--goal",
                "Add retry behavior",
                "--input",
                "network client",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            run_dir = Path(payload["run_dir"])
            self.assertTrue(run_dir.is_dir())
            self.assertTrue(run_dir.is_relative_to((out / "runs").resolve()))
            self.assertTrue((run_dir / "handoff.md").is_file())

    def test_default_output_dir_avoids_plugin_home_when_dispatched_from_install_tree(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            proc = self._run(
                "test-driven-development",
                "--goal",
                "Add retry behavior",
                cwd=PLUGIN_PATH,
                env={"HOME": str(home)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            run_dir = Path(payload["run_dir"])
            self.assertFalse(run_dir.is_relative_to(PLUGIN_PATH.resolve()))
            expected_root = home / ".ouroboros" / "plugin-artifacts" / "superpowers" / "runs"
            self.assertTrue(run_dir.is_relative_to(expected_root.resolve()))
            self.assertTrue((run_dir / "audit.jsonl").is_file())

    def test_dispatcher_output_env_selects_workspace_artifact_root(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "runtime-output"
            proc = self._run(
                "verification-before-completion",
                "--goal",
                "Verify retry behavior",
                cwd=PLUGIN_PATH,
                env={"OUROBOROS_PLUGIN_OUTPUT_DIR": str(out)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            run_dir = Path(payload["run_dir"])
            self.assertTrue(run_dir.is_relative_to((out / "runs").resolve()))
            self.assertFalse(run_dir.is_relative_to(PLUGIN_PATH.resolve()))

    def test_output_priority_and_subcommand_override(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {"OUROBOROS_PLUGIN_OUTPUT_DIR": str(root / "env-output"),
                   "OUROBOROS_PLUGIN_WORKDIR": str(root / "workdir")}
            cases = [
                (("list",), root / "env-output", env),
                (("--output-dir", str(root / "before"), "list"), root / "before", env),
                (("--output-dir", str(root / "before"), "list", "--output-dir", str(root / "after")), root / "after", env),
                (("list",), root / "workdir" / ".omx" / "superpowers", {**env, "OUROBOROS_PLUGIN_OUTPUT_DIR": ""}),
                (("list",), root / ".omx" / "superpowers", {"OUROBOROS_PLUGIN_OUTPUT_DIR": "", "OUROBOROS_PLUGIN_WORKDIR": ""}),
            ]
            for argv, expected, variables in cases:
                with self.subTest(argv=argv, expected=expected):
                    proc = self._run(*argv, cwd=root, env=variables)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(Path(json.loads(proc.stdout)["skill_index_path"]).parent, expected)

    def test_unknown_skill_is_rejected(self):
        proc = self._run("inspect", "not-a-skill")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown Superpowers skill", proc.stderr)


if __name__ == "__main__":
    unittest.main()
