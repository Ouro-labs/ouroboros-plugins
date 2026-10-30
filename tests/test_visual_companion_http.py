from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request


SERVER = Path(__file__).resolve().parents[1] / "plugins/visual-companion/assets/skill/scripts/server.cjs"


@unittest.skipIf(shutil.which("node") is None, "Node.js is required for HTTP boundary tests")
class VisualCompanionHTTPTests(unittest.TestCase):
    @contextmanager
    def server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "node.log").open("w+") as log:
                proc = subprocess.Popen(
                    ["node", str(SERVER)],
                    env={
                        **os.environ,
                        "BRAINSTORM_DIR": str(root),
                        "BRAINSTORM_PORT": "",
                        "BRAINSTORM_HOST": "127.0.0.1",
                        "BRAINSTORM_URL_HOST": "127.0.0.1",
                        "BRAINSTORM_OWNER_PID": "",
                        "BRAINSTORM_TOKEN": "http-regression-token",
                    },
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 5
                    info_path = root / "state/server-info"
                    while not info_path.exists():
                        self.assertIsNone(proc.poll(), "Node exited before startup")
                        self.assertLess(time.monotonic(), deadline, "Node startup timed out")
                        time.sleep(0.01)
                    info = json.loads(info_path.read_text())
                    yield root, proc, info
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=2)
                    log.seek(0)
                    output = log.read()
                    if proc.returncode != 0:
                        print(output)

    def get(self, info, path):
        try:
            with urllib.request.urlopen(info["url"] + path, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def test_directory_assets_return_404_and_server_remains_available(self):
        with self.server() as (root, proc, info):
            (root / "content/nested").mkdir()
            (root / "content/ok.css").write_text("body { color: black; }")
            for path in ("/files/", "/files/nested", "/files/..", "/files/missing.css"):
                with self.subTest(path=path):
                    self.assertEqual(self.get(info, path)[0], 404)
                    self.assertEqual(self.get(info, "/")[0], 200)
                    self.assertIsNone(proc.poll())
            self.assertEqual(self.get(info, "/files/ok.css"), (200, b"body { color: black; }"))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX named pipes required")
    def test_non_regular_asset_does_not_block_http(self):
        with self.server() as (root, proc, info):
            os.mkfifo(root / "content/pipe")
            self.assertEqual(self.get(info, "/files/pipe")[0], 404)
            self.assertEqual(self.get(info, "/")[0], 200)
            self.assertIsNone(proc.poll())

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "POSIX file permissions required")
    def test_unreadable_asset_returns_404_and_server_remains_available(self):
        with self.server() as (root, proc, info):
            asset = root / "content/private.css"
            asset.write_text("body {}")
            asset.chmod(0)
            try:
                self.assertEqual(self.get(info, "/files/private.css")[0], 404)
                self.assertEqual(self.get(info, "/")[0], 200)
                self.assertIsNone(proc.poll())
            finally:
                asset.chmod(0o600)

    def test_stop_exits_owned_child_with_incomplete_http_connections(self):
        for partial_request in (
            b"GET / HTTP/1.1\r\nHost: incomplete\r\n",
            b"POST /__visual_companion_event HTTP/1.1\r\n",
            b"",
        ):
            with self.subTest(partial_request=partial_request), self.server() as (root, proc, info):
                with socket.create_connection(("127.0.0.1", info["port"]), timeout=2) as pending:
                    pending.sendall(partial_request)
                    self.assertIsNone(proc.poll())
                    req = urllib.request.Request(
                        info["url"] + "/__visual_companion_stop",
                        data=b"",
                        headers={"x-visual-companion-token": "http-regression-token"},
                    )
                    with urllib.request.urlopen(req, timeout=2) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(response.read(), b"Stopping")
                    self.assertEqual(proc.wait(timeout=3), 0)
                    self.assertEqual(pending.recv(1), b"")
                    self.assertTrue((root / "state/server-stopped").is_file())
                    self.assertFalse((root / "state/server-info").exists())


if __name__ == "__main__":
    unittest.main()
