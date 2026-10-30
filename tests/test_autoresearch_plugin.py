from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN_PATH = REPO / "plugins" / "autoresearch"


class AutoresearchPluginTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "ouroboros_autoresearch", *args],
            cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(PLUGIN_PATH)},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_inspect_reports_ready_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "program.md").write_text("Improve the model.\n", encoding="utf-8")
            (root / "prepare.py").write_text("MAX_SEQ_LEN = 1024\n", encoding="utf-8")
            (root / "train.py").write_text("print('val_bpb=1.0')\n", encoding="utf-8")

            proc = self._run("inspect", str(root))

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["ooo_auto_ready"])
        self.assertEqual(payload["missing"], [])

    def test_prepare_writes_seed_and_handoff(self):
        with tempfile.TemporaryDirectory(prefix="autoresearch path with spaces ") as td:
            root = Path(td)
            (root / "program.md").write_text(
                "Improve val_bpb.\n\n```python\nprint('example')\n```\n",
                encoding="utf-8",
            )
            (root / "prepare.py").write_text("MAX_SEQ_LEN = 1024\n", encoding="utf-8")
            (root / "train.py").write_text("print('val_bpb=1.0')\n", encoding="utf-8")

            proc = self._run(
                "prepare",
                str(root),
                "--goal",
                "Find a smaller validation bpb.",
                "--max-experiments",
                "2",
                "--experiment-seconds",
                "60",
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            seed_path = Path(payload["seed_path"])
            auto_goal_path = Path(payload["auto_goal_path"])
            handoff_path = Path(payload["handoff_path"])
            self.assertTrue(seed_path.is_file())
            self.assertTrue(auto_goal_path.is_file())
            self.assertTrue(handoff_path.is_file())
            seed = seed_path.read_text(encoding="utf-8")
            auto_goal = auto_goal_path.read_text(encoding="utf-8")
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertIn("Find a smaller validation bpb.", seed)
            self.assertIn("Run at most 2 experiments.", seed)
            self.assertIn("Keep each experiment bounded to 60 seconds.", seed)
            self.assertIn("## Experiment Plan", seed)
            self.assertIn("Experiment 1 is the unmodified baseline run", seed)
            self.assertIn("## Authoritative Autoresearch Contract", seed)
            self.assertIn('"repository":', seed)
            self.assertIn('"candidate_sequence":', seed)
            self.assertIn('"name": "baseline"', seed)
            self.assertIn('"name": "additive-smoothing"', seed)
            self.assertIn('"non_goals":', seed)
            self.assertIn('"runtime_context":', seed)
            self.assertIn('"validity_rules":', seed)
            self.assertIn('"verification_plan":', seed)
            self.assertIn('"execution_command":', seed)
            self.assertIn('"seed_artifact_policy":', seed)
            self.assertIn('"saved_seed_path_runtime_owned": true', seed)
            self.assertIn("normal Ouroboros YAML/Seed serialization", seed)
            self.assertIn("Do not create or require `.ouroboros/autoresearch/seed.yaml`", seed)
            self.assertIn("must not be declared as a target-repository output", seed)
            self.assertIn("keep the command string exactly `uv run train.py`", seed)
            self.assertIn("baseline-only rerun", seed)
            self.assertIn("final best", seed)
            self.assertIn("plugin-owned extra fields for autoresearch-specific values", seed)
            self.assertIn("## Non-Goals", seed)
            self.assertIn("## Runtime Context", seed)
            self.assertIn("`prepare.py` as fixed data prep", seed)
            self.assertIn("````markdown", seed)
            self.assertIn("Use the prepared autoresearch handoff brief", auto_goal)
            self.assertIn("Experiments 2-2 must be concrete candidate changes", auto_goal)
            self.assertIn("Include top-level non_goals", auto_goal)
            self.assertIn("Include runtime_context", auto_goal)
            self.assertIn("Include seed_artifact_policy", auto_goal)
            self.assertIn("Do not declare top-level `seed_artifact_path`", auto_goal)
            self.assertIn("do not rewrite the command into a shell-specific timeout wrapper", auto_goal)
            self.assertIn("Do not run training during Seed creation", auto_goal)
            self.assertIn("Authoritative autoresearch_contract JSON follows", auto_goal)
            self.assertIn("plugin-owned extra fields for autoresearch-specific values", auto_goal)
            self.assertIn('"experiment_budget":2', auto_goal)
            self.assertIn('"legacy_json_fallback":"best_val_bpb"', auto_goal)
            self.assertIn('"repo_local_seed_output_required":false', auto_goal)
            self.assertIn('"verification_plan":', auto_goal)
            self.assertIn("normal Ouroboros YAML/Seed serialization", auto_goal)
            self.assertIn("baseline-only rerun", auto_goal)
            self.assertEqual(handoff, payload)
            self.assertIn("ouroboros auto \"$(cat", payload["ooo_auto"]["recommended_command"])
            self.assertIn("autoresearch path with spaces", payload["ooo_auto"]["recommended_command"])
            self.assertIn("'", payload["ooo_auto"]["recommended_command"])
            self.assertEqual(payload["ooo_auto"]["editable_files"], ["train.py"])
            self.assertEqual(
                payload["ooo_auto"]["autoresearch_contract"]["candidate_sequence"][0]["name"],
                "baseline",
            )
            self.assertEqual(
                payload["ooo_auto"]["autoresearch_contract"]["candidate_sequence"][1]["name"],
                "additive-smoothing",
            )
            self.assertFalse(
                payload["ooo_auto"]["autoresearch_contract"]["seed_artifact_policy"][
                    "repo_local_seed_output_required"
                ]
            )

    def test_prepare_requires_autoresearch_files(self):
        with tempfile.TemporaryDirectory() as td:
            proc = self._run("prepare", td, "--goal", "Improve the model.")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("missing required file", proc.stderr)

    def test_layout_paths_must_stay_inside_repository(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "program.md").write_text("Improve the model.\n", encoding="utf-8")
            (root / "prepare.py").write_text("MAX_SEQ_LEN = 1024\n", encoding="utf-8")
            (root / "train.py").write_text("print('val_bpb=1.0')\n", encoding="utf-8")

            proc = self._run("inspect", str(root), "--program-file", "../program.md")

        self.assertEqual(proc.returncode, 2)
        self.assertIn("--program-file must stay inside", proc.stderr)

    def test_layout_paths_must_be_relative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "program.md").write_text("Improve the model.\n", encoding="utf-8")
            (root / "prepare.py").write_text("MAX_SEQ_LEN = 1024\n", encoding="utf-8")
            (root / "train.py").write_text("print('val_bpb=1.0')\n", encoding="utf-8")

            proc = self._run("inspect", str(root), "--program-file", str(root / "program.md"))

        self.assertEqual(proc.returncode, 2)
        self.assertIn("--program-file must be a path relative", proc.stderr)


if __name__ == "__main__":
    unittest.main()
