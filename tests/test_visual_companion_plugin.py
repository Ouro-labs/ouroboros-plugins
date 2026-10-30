from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN_PATH = REPO / "plugins" / "visual-companion"
MANIFEST = PLUGIN_PATH / "ouroboros.plugin.json"


class VisualCompanionPluginTests(unittest.TestCase):
    def _run(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        run_env = {**os.environ, "PYTHONPATH": str(PLUGIN_PATH)}
        if env:
            run_env.update(env)
        return subprocess.run(
            [sys.executable, "-m", "visual_companion_plugin", *args],
            cwd=cwd or REPO,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_manifest_declares_bridge_commands_and_permissions(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "0.1")
        self.assertEqual(manifest["name"], "visual-companion")
        self.assertEqual({command["name"] for command in manifest["commands"]}, {"start", "show", "wait", "read", "stop"})
        self.assertEqual({command["namespace"] for command in manifest["commands"]}, {"visual-companion"})
        self.assertEqual(
            {permission["scope"] for permission in manifest["permissions"]},
            {"filesystem:read", "filesystem:write", "shell:execute"},
        )
        self.assertEqual(manifest["entrypoint"]["command"], "python -m visual_companion_plugin")

    def test_command_help_surfaces_parse(self):
        for command in ("start", "show", "wait", "read", "stop"):
            proc = self._run(command, "--help")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(command, proc.stdout)

    def test_missing_state_dir_returns_blocked_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            proc = self._run("read", "--state-dir", str(Path(td) / "missing"), cwd=Path(td))
            self.assertEqual(proc.returncode, 2)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["plugin"]["name"], "visual-companion")
            handoff = Path(td) / payload["handoff"]
            self.assertTrue(handoff.is_file())
            handoff_payload = json.loads(handoff.read_text(encoding="utf-8"))
            self.assertEqual(handoff_payload["status"], "blocked")
            self.assertIn("filesystem:read", handoff_payload["permissions_used"])

    def test_output_dir_keeps_default_artifacts_outside_plugin_home(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td) / "output"
            proc = self._run(
                "read",
                env={"OUROBOROS_PLUGIN_OUTPUT_DIR": str(output_root)},
                cwd=PLUGIN_PATH,
            )
            self.assertEqual(proc.returncode, 2)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "blocked")
            self.assertTrue(Path(payload["handoff"]).is_relative_to(output_root))
            self.assertFalse((PLUGIN_PATH / ".omx" / "handoffs" / "visual-companion").exists())

    def test_show_creates_fresh_screen_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".brainstorm" / "session" / "state"
            content = root / ".brainstorm" / "session" / "content"
            state.mkdir(parents=True)
            content.mkdir(parents=True)
            (state / "server-info").write_text(json.dumps({"url": "http://localhost:1"}) + "\n", encoding="utf-8")
            html = root / "layout.html"
            html.write_text("<h2>Pick one</h2>\n", encoding="utf-8")

            first = self._run("show", "--html", str(html), "--state-dir", str(state), cwd=root)
            second = self._run("show", "--html", str(html), "--state-dir", str(state), cwd=root)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertNotEqual(first_payload["screen"]["path"], second_payload["screen"]["path"])
            self.assertTrue(Path(first_payload["screen"]["path"]).is_file())
            self.assertTrue(Path(second_payload["screen"]["path"]).is_file())

    @unittest.skipIf(shutil.which("node") is None, "Node.js is required for visual companion lifecycle smoke")
    def test_start_show_read_wait_stop_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            start = self._run("start", "--project-dir", str(root), cwd=root)
            self.assertEqual(start.returncode, 0, start.stderr)
            session = json.loads(start.stdout)["session"]
            state_dir = Path(session["state_dir"])
            session_dir = Path(session["session_dir"])
            url = session["url"]
            self.assertNotIn("token", session)
            private_session = json.loads((state_dir / "adapter-session.json").read_text(encoding="utf-8"))
            token = private_session["token"]

            try:
                html = root / "screen.html"
                html.write_text(
                    "<h2>Pick one</h2><div class=\"option\" data-choice=\"a\" onclick=\"toggleSelect(this)\">A</div>\n",
                    encoding="utf-8",
                )
                show = self._run("show", "--html", str(html), "--state-dir", str(state_dir), cwd=root)
                self.assertEqual(show.returncode, 0, show.stderr)

                req = urllib.request.Request(
                    f"{url}/__visual_companion_event",
                    data=json.dumps({"type": "click", "choice": "a", "eventId": "test-event-1", "screenId": Path(json.loads(show.stdout)["screen"]["path"]).name}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "x-visual-companion-token": token},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    self.assertEqual(response.status, 202)

                read = self._run("read", "--state-dir", str(state_dir), cwd=root)
                self.assertEqual(read.returncode, 0, read.stderr)
                read_payload = json.loads(read.stdout)
                self.assertEqual(read_payload["event"]["status"], "answered")
                self.assertEqual(read_payload["event"]["event"]["choice"], "a")

                wait = self._run("wait", "--state-dir", str(state_dir), "--timeout-ms", "1000", cwd=root)
                self.assertEqual(wait.returncode, 0, wait.stderr)
                wait_payload = json.loads(wait.stdout)
                self.assertEqual(wait_payload["event"]["choice"], "a")
            finally:
                stop = self._run("stop", "--session-dir", str(session_dir), cwd=root)
                self.assertEqual(stop.returncode, 0, stop.stderr)


if __name__ == "__main__":
    unittest.main()
