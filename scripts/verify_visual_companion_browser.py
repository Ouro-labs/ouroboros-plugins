"""Exercise the real Node server and Chromium without model inference.

Run with a Python environment containing Playwright and an installed Chromium.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "plugins/visual-companion",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = {
            **os.environ,
            "PYTHONPATH": str(args.plugin_root.resolve()),
            "OUROBOROS_PLUGIN_OUTPUT_DIR": str(root),
        }

        def run(*argv, code=0):
            proc = subprocess.run(
                [sys.executable, "-m", "visual_companion_plugin", *argv],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert proc.returncode == code, (
                argv,
                proc.returncode,
                proc.stdout,
                proc.stderr,
            )
            return json.loads(proc.stdout)

        start = run("start", "--project-dir", str(root))
        session = start["session"]
        state = Path(session["state_dir"])
        private = json.loads((state / "adapter-session.json").read_text())
        token = private["token"]
        assert token not in json.dumps(start)

        def post(event, auth=token, headers=None):
            req = urllib.request.Request(
                session["url"] + "/__visual_companion_event",
                data=json.dumps(event).encode(),
                headers={
                    "Content-Type": "application/json",
                    "x-visual-companion-token": auth,
                    **(headers or {}),
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as exc:
                return exc.code, None

        try:
            html = root / "question.html"
            html.write_text(
                '<button data-choice="a">Choose A</button><form data-brainstorm-form><input name="answer" aria-label="Answer"><button type="submit" data-submit>Submit</button></form>'
            )
            show = run("show", "--html", str(html), "--state-dir", str(state))
            screen = Path(show["screen"]["path"]).name
            event = {
                "type": "click",
                "choice": "bad",
                "eventId": "wrong-token",
                "screenId": screen,
            }
            assert post(event, "wrong")[0] == 403
            assert post(event, headers={"Origin": "http://evil.invalid"})[0] == 403
            assert post(event, headers={"Host": "evil.invalid"})[0] == 403
            assert post({**event, "screenId": "wrong.html"})[1]["accepted"] == 0
            print("PASS token, origin, host and wrong-screen rejection")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(session["url"])
                    expect(page.get_by_role("button", name="Choose A")).to_be_visible()
                    status = page.evaluate("""async () => await new Promise(resolve => {
                        const socket = new WebSocket('ws://' + location.host + '/?token=wrong');
                        socket.onerror = () => resolve('rejected');
                        socket.onopen = () => { socket.close(); resolve('accepted'); };
                    })""")
                    assert status == "rejected"
                    page.evaluate("""async () => await new Promise(resolve => {
                        const socket = new WebSocket('ws://' + location.host + '/?token=' + window.__VISUAL_COMPANION_TOKEN);
                        socket.onopen = () => { socket.send('null'); socket.send('[]'); socket.close(); resolve(); };
                    })""")
                    page.get_by_role("button", name="Choose A").click()
                    answer = run(
                        "wait",
                        "--state-dir",
                        str(state),
                        "--type",
                        "click",
                        "--timeout-ms",
                        "3000",
                    )["event"]
                    assert answer["choice"] == "a" and answer["screenId"] == screen
                    assert (
                        run("read", "--state-dir", str(state))["event"]["event"][
                            "eventId"
                        ]
                        == answer["eventId"]
                    )
                    assert post(answer)[1]["accepted"] == 0
                    events = [
                        json.loads(line)
                        for line in (state / "events").read_text().splitlines()
                    ]
                    assert (
                        len([e for e in events if e["eventId"] == answer["eventId"]])
                        == 1
                    )
                    print(
                        "PASS real Chromium click, websocket/HTTP deduplication and durable read"
                    )
                    second = run("show", "--html", str(html), "--state-dir", str(state))
                    new_screen = Path(second["screen"]["path"]).name
                    assert new_screen != screen
                    assert (
                        run("read", "--state-dir", str(state))["event"]["status"]
                        == "pending"
                    )
                    assert (
                        post({**answer, "eventId": "stale-screen"})[1]["accepted"] == 0
                    )
                    assert (
                        run(
                            "wait",
                            "--state-dir",
                            str(state),
                            "--type",
                            "submit",
                            "--timeout-ms",
                            "200",
                            code=1,
                        )["status"]
                        == "failed"
                    )
                    page.goto(session["url"])
                    page.get_by_label("Answer").fill("compact")
                    page.get_by_role("button", name="Submit", exact=True).click()
                    form = run(
                        "wait",
                        "--state-dir",
                        str(state),
                        "--type",
                        "submit",
                        "--timeout-ms",
                        "3000",
                    )["event"]
                    assert (
                        form["fields"] == {"answer": "compact"}
                        and form["screenId"] == new_screen
                    )
                    print(
                        "PASS stale answer isolation, wrong-type timeout and real form submission"
                    )
                    forged = {
                        **form,
                        "eventId": "forged-time",
                        "timestamp": 1,
                        "source": "forged",
                    }
                    assert post(forged)[1]["accepted"] == 1
                    recovered = run("read", "--state-dir", str(state))["event"]["event"]
                    assert (
                        recovered["timestamp"] > 1 and recovered["source"] == "browser"
                    )
                    (state / "server.pid").write_text(str(os.getpid()))
                    assert (
                        run("stop", "--state-dir", str(state), code=2)["status"]
                        == "blocked"
                    )
                    (state / "server.pid").write_text(str(private["pid"]))
                    stop = run("stop", "--state-dir", str(state))
                    assert stop["stop"]["status"] == "stopped"
                    assert (state / "server-stopped").is_file() and not (
                        state / "server-info"
                    ).exists()
                    assert (
                        run("stop", "--state-dir", str(state))["stop"]["status"]
                        == "not_running"
                    )
                    print(
                        "PASS server-owned timestamps, foreign-pid refusal, open-browser shutdown and repeat stop"
                    )
                finally:
                    browser.close()
            for path in (root / ".omx").rglob("*.json"):
                assert token not in path.read_text(), path
            assert token not in (state / "server.log").read_text()
            print(
                "PASS no token in stdout, handoffs or server log; no inference or training"
            )
        finally:
            if (state / "server-info").exists():
                run("stop", "--state-dir", str(state))


if __name__ == "__main__":
    main()
