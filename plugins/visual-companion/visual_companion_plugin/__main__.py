"""Command entrypoint for the visual-companion Ouroboros plugin."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import PLUGIN_NAME, PLUGIN_VERSION

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "assets" / "skill"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
SAFE_USER_ARTIFACT_ROOT = Path.home() / ".ouroboros" / "plugin-artifacts" / PLUGIN_NAME


@dataclass(frozen=True)
class CommandResult:
    status: str
    command: str
    payload: dict[str, Any]
    returncode: int = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def print_json(data: dict[str, Any]) -> None:
    sys.stdout.write(dumps(data) + "\n")


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def default_artifact_root() -> Path:
    output_dir = os.environ.get("OUROBOROS_PLUGIN_OUTPUT_DIR")
    if output_dir:
        return Path(output_dir).expanduser().resolve()

    workdir = os.environ.get("OUROBOROS_PLUGIN_WORKDIR")
    if workdir:
        return Path(workdir).expanduser().resolve()

    cwd = Path.cwd().resolve()
    if path_is_relative_to(cwd, PLUGIN_ROOT):
        return SAFE_USER_ARTIFACT_ROOT.resolve()
    return cwd


def resolve_artifact_root(value: str | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return default_artifact_root()


def handoff_dir() -> Path:
    return resolve_artifact_root() / ".omx" / "handoffs" / PLUGIN_NAME


def blocked(command: str, reason: str, *, details: dict[str, Any] | None = None) -> CommandResult:
    payload: dict[str, Any] = {
        "schema_version": "visual-companion.result.v0.1",
        "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
        "command": command,
        "status": "blocked",
        "reason": reason,
        "details": details or {},
        "created_at": now_iso(),
    }
    return CommandResult("blocked", command, payload, 2)


def completed(command: str, payload: dict[str, Any]) -> CommandResult:
    base = {
        "schema_version": "visual-companion.result.v0.1",
        "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
        "command": command,
        "status": "completed",
        "created_at": now_iso(),
    }
    base.update(payload)
    return CommandResult("completed", command, base, 0)


def failed(command: str, reason: str, *, details: dict[str, Any] | None = None) -> CommandResult:
    payload: dict[str, Any] = {
        "schema_version": "visual-companion.result.v0.1",
        "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
        "command": command,
        "status": "failed",
        "reason": reason,
        "details": details or {},
        "created_at": now_iso(),
    }
    return CommandResult("failed", command, payload, 1)


def write_handoff(result: CommandResult) -> dict[str, Any]:
    root = handoff_dir()
    root.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    path = root / f"{run_id}-{result.command}.json"
    handoff = {
        "schema_version": "visual-companion.handoff.v0.1",
        "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
        "run_id": run_id,
        "status": result.status,
        "command": result.command,
        "permissions_used": permissions_for(result.command),
        "capabilities_used": ["state:write", "provenance:write", "handoff:attach", "progress:write"],
        "result": result.payload,
        "created_at": now_iso(),
    }
    path.write_text(dumps(handoff) + "\n", encoding="utf-8")
    result.payload["handoff"] = str(path)
    return result.payload


def permissions_for(command: str) -> list[str]:
    return ["filesystem:read", "filesystem:write", "shell:execute"]


def require_node(command: str) -> str | CommandResult:
    node = shutil.which("node")
    if node is None:
        return blocked(command, "Node.js is required to run the bundled visual companion scripts.")
    return node


def script_path(name: str) -> Path:
    return SCRIPTS_DIR / name


def require_script(command: str, name: str) -> Path | CommandResult:
    path = script_path(name)
    if not path.is_file():
        return blocked(command, f"Bundled script is missing: {path}")
    return path


def resolve_existing_path(command: str, value: str) -> Path | CommandResult:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        return blocked(command, f"Path does not exist: {path}")
    return path


def session_dir_from_state(state_dir: Path) -> Path:
    return state_dir.parent


def latest_session(project_dir: Path) -> Path | None:
    root = project_dir / ".brainstorm"
    if not root.is_dir():
        return None
    sessions = [p for p in root.iterdir() if (p / "state").is_dir()]
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def resolve_state_dir(command: str, state_dir: str | None, project_dir: str | None = None) -> Path | CommandResult:
    if state_dir:
        state = Path(state_dir).expanduser().resolve()
    else:
        project = resolve_artifact_root(project_dir)
        session = latest_session(project)
        if session is None:
            return blocked(command, "No --state-dir was provided and no .brainstorm session was found.", details={"project_dir": str(project)})
        state = session / "state"
    if not state.is_dir():
        return blocked(command, f"State directory does not exist: {state}")
    return state


def resolve_session_dir(command: str, session_dir: str | None, state_dir: str | None = None, project_dir: str | None = None) -> Path | CommandResult:
    if session_dir:
        session = Path(session_dir).expanduser().resolve()
    elif state_dir:
        state = resolve_state_dir(command, state_dir, project_dir)
        if isinstance(state, CommandResult):
            return state
        session = session_dir_from_state(state)
    else:
        project = resolve_artifact_root(project_dir)
        latest = latest_session(project)
        if latest is None:
            return blocked(command, "No --session-dir was provided and no .brainstorm session was found.", details={"project_dir": str(project)})
        session = latest.resolve()
    if not session.is_dir():
        return blocked(command, f"Session directory does not exist: {session}")
    return session


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_node(command: str, script: Path, args: list[str], timeout: int | None = None) -> CommandResult:
    node = require_node(command)
    if isinstance(node, CommandResult):
        return node
    try:
        proc = subprocess.run(
            [node, str(script), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return failed(command, "Timed out while running bundled Node script.", details={"script": str(script), "timeout": timeout})
    if proc.returncode != 0:
        return failed(command, "Bundled Node script failed.", details={"script": str(script), "returncode": proc.returncode, "stderr": proc.stderr.strip(), "stdout": proc.stdout.strip()})
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout.strip()}
    return completed(command, {"event": payload, "script": str(script)})


def command_start(args: argparse.Namespace) -> CommandResult:
    command = "start"
    node = require_node(command)
    if isinstance(node, CommandResult):
        return node
    server = require_script(command, "server.cjs")
    if isinstance(server, CommandResult):
        return server

    project_dir = resolve_artifact_root(args.project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"{os.getpid()}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    session_dir = project_dir / ".brainstorm" / session_id
    content_dir = session_dir / "content"
    state_dir = session_dir / "state"
    content_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)

    token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    env = os.environ.copy()
    env.update(
        {
            "BRAINSTORM_DIR": str(session_dir),
            "BRAINSTORM_HOST": args.host,
            "BRAINSTORM_URL_HOST": args.url_host or ("localhost" if args.host in {"127.0.0.1", "localhost"} else args.host),
            "BRAINSTORM_TOKEN": token,
        }
    )

    log_file = state_dir / "server.log"
    log_handle = log_file.open("w", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [node, str(server)],
            cwd=str(SCRIPTS_DIR),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_handle.close()
        return failed(command, "Failed to start visual companion server.", details={"error": str(exc)})
    finally:
        try:
            log_handle.close()
        except OSError:
            pass

    (state_dir / "server.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    info_file = state_dir / "server-info"
    info = None
    for _ in range(80):
        if proc.poll() is not None:
            return failed(command, "Visual companion server exited before startup completed.", details={"returncode": proc.returncode, "log": safe_read(log_file)})
        info = read_json_file(info_file)
        if info is not None:
            break
        time.sleep(0.1)
    if info is None:
        proc.terminate()
        proc.wait(timeout=5)
        return failed(command, "Visual companion server did not write server-info before timeout.", details={"pid": proc.pid, "log": safe_read(log_file)})

    session_info = {
        **info,
        "session_id": session_id,
        "session_dir": str(session_dir),
        "content_dir": str(content_dir),
        "state_dir": str(state_dir),
        "pid": proc.pid,
    }
    private_session_info = {**session_info, "token": token}
    (state_dir / "adapter-session.json").write_text(dumps(private_session_info) + "\n", encoding="utf-8")
    (state_dir / "adapter-session.json").chmod(0o600)
    return completed(command, {"session": session_info, "artifacts": {"server_info": str(info_file), "log": str(log_file)}})


def safe_read(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def command_show(args: argparse.Namespace) -> CommandResult:
    command = "show"
    state = resolve_state_dir(command, args.state_dir, args.project_dir)
    if isinstance(state, CommandResult):
        return state
    html = resolve_existing_path(command, args.html)
    if isinstance(html, CommandResult):
        return html
    if not html.is_file():
        return blocked(command, f"HTML input is not a file: {html}")

    session = session_dir_from_state(state)
    content_dir = session / "content"
    if not content_dir.is_dir():
        return blocked(command, f"Content directory does not exist: {content_dir}")
    info_file = state / "server-info"
    stopped_file = state / "server-stopped"
    if not info_file.is_file() or stopped_file.exists():
        return blocked(command, "Visual companion server is not active for this state directory.", details={"state_dir": str(state)})

    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in (args.name or html.stem)).strip("-") or "screen"
    target = unique_html_path(content_dir, stem)
    shutil.copyfile(html, target)
    marker = {"id": target.name, "screenId": target.name, "status": "pending", "sinceMs": int(time.time() * 1000)}
    (state / "pending-question.json").write_text(dumps(marker) + "\n", encoding="utf-8")
    return completed(command, {"screen": {"source": str(html), "path": str(target), "state_dir": str(state), "session_dir": str(session)}})


def unique_html_path(content_dir: Path, stem: str) -> Path:
    candidate = content_dir / f"{stem}.html"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = content_dir / f"{stem}-{index}.html"
        if not candidate.exists():
            return candidate
        index += 1


def command_wait(args: argparse.Namespace) -> CommandResult:
    command = "wait"
    state = resolve_state_dir(command, args.state_dir, args.project_dir)
    if isinstance(state, CommandResult):
        return state
    script = require_script(command, "wait-for-event.cjs")
    if isinstance(script, CommandResult):
        return script
    node_args = [str(state), "--timeout-ms", str(args.timeout_ms)]
    if args.clear:
        node_args.append("--clear")
    if args.type:
        node_args.extend(["--type", args.type])
    timeout = None if args.timeout_ms == 0 else max(1, int(args.timeout_ms / 1000) + 5)
    return run_node(command, script, node_args, timeout=timeout)


def command_read(args: argparse.Namespace) -> CommandResult:
    command = "read"
    state = resolve_state_dir(command, args.state_dir, args.project_dir)
    if isinstance(state, CommandResult):
        return state
    script = require_script(command, "read-pending-event.cjs")
    if isinstance(script, CommandResult):
        return script
    node_args = [str(state)]
    if args.type:
        node_args.extend(["--type", args.type])
    return run_node(command, script, node_args, timeout=20)


def command_stop(args: argparse.Namespace) -> CommandResult:
    command = "stop"
    session = resolve_session_dir(command, args.session_dir, args.state_dir, args.project_dir)
    if isinstance(session, CommandResult):
        return session
    state = session / "state"
    pid_file = state / "server.pid"
    if not pid_file.is_file():
        (state / "server-stopped").write_text(dumps({"reason": "not_running", "timestamp": int(time.time() * 1000)}) + "\n", encoding="utf-8")
        return completed(command, {"stop": {"status": "not_running", "session_dir": str(session)}})
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return failed(command, "Invalid server pid file.", details={"pid_file": str(pid_file)})

    private = read_json_file(state / "adapter-session.json")
    if not private or not private.get("token") or private.get("pid") != pid:
        return blocked(command, "Cannot authenticate the session owner; refusing to signal a pid.")
    request = urllib.request.Request(
        private["url"] + "/__visual_companion_stop", data=b"",
        headers={"x-visual-companion-token": private["token"]}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 202:
                return failed(command, "Server refused shutdown.")
    except (OSError, urllib.error.URLError):
        if (state / "server-stopped").is_file():
            return completed(command, {"stop": {"status": "not_running", "session_dir": str(session)}})
        return blocked(command, "Session server is unreachable; no process was signalled.")
    for _ in range(50):
        if (state / "server-stopped").is_file() and not (state / "server-info").exists():
            return completed(command, {"stop": {"status": "stopped", "session_dir": str(session)}})
        time.sleep(0.1)
    return failed(command, "Server did not confirm shutdown.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="visual_companion_plugin",
        description="Serve visual question screens and read browser events.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a local visual companion server")
    start.add_argument("--project-dir")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--url-host")
    start.set_defaults(func=command_start)

    show = sub.add_parser("show", help="Publish an HTML screen")
    show.add_argument("--html", required=True)
    show.add_argument("--state-dir")
    show.add_argument("--project-dir")
    show.add_argument("--name")
    show.set_defaults(func=command_show)

    wait = sub.add_parser("wait", help="Wait for a browser event")
    wait.add_argument("--state-dir")
    wait.add_argument("--project-dir")
    wait.add_argument("--timeout-ms", type=int, default=1_800_000)
    wait.add_argument("--clear", action="store_true")
    wait.add_argument("--type")
    wait.set_defaults(func=command_wait)

    read = sub.add_parser("read", help="Read a pending browser event")
    read.add_argument("--state-dir")
    read.add_argument("--project-dir")
    read.add_argument("--type")
    read.set_defaults(func=command_read)

    stop = sub.add_parser("stop", help="Stop a visual companion session")
    stop.add_argument("--session-dir")
    stop.add_argument("--state-dir")
    stop.add_argument("--project-dir")
    stop.set_defaults(func=command_stop)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    payload = write_handoff(result)
    print_json(payload)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
