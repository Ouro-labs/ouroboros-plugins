# visual-companion

Ouroboros plugin adapter for the `visual-companion` Codex skill.

The plugin is intentionally an I/O bridge, not a design generator. Agents or
workflows author HTML screens; this plugin serves those screens in a local
browser session, receives click/form events, and records JSON handoff evidence.

## Commands

```bash
ooo visual-companion start --project-dir .
ooo visual-companion show --html path/to/screen.html --state-dir <state-dir>
ooo visual-companion wait --state-dir <state-dir> --timeout-ms 1800000
ooo visual-companion read --state-dir <state-dir>
ooo visual-companion stop --session-dir <session-dir>
```

Direct adapter invocation during development:

```bash
PYTHONPATH=plugins/visual-companion python -m visual_companion_plugin start --project-dir .
PYTHONPATH=plugins/visual-companion python -m visual_companion_plugin show --html screen.html --state-dir .brainstorm/<session>/state
PYTHONPATH=plugins/visual-companion python -m visual_companion_plugin wait --state-dir .brainstorm/<session>/state
PYTHONPATH=plugins/visual-companion python -m visual_companion_plugin read --state-dir .brainstorm/<session>/state
PYTHONPATH=plugins/visual-companion python -m visual_companion_plugin stop --session-dir .brainstorm/<session>
```

## Install and trust

From this repository root:

```bash
ouroboros plugin add . --plugin visual-companion
ouroboros plugin trust visual-companion --scope filesystem:read --scope filesystem:write --scope shell:execute
```

## Output artifacts

Each invocation writes handoff evidence under the resolved output root:

```text
<output-root>/.omx/handoffs/visual-companion/
```

The visual session itself is persisted under `<output-root>/.brainstorm/`.
`--project-dir` takes precedence. Without it, the adapter prefers
`OUROBOROS_PLUGIN_OUTPUT_DIR`, then `OUROBOROS_PLUGIN_WORKDIR`, then the current
working directory. If the current working directory is the installed plugin
home, artifacts fall back to a user-local Ouroboros artifact directory so the
trusted plugin home is not modified at runtime.

Add `.brainstorm/` to the target project's ignore rules when those screens
should remain local-only.

## Non-goals

- The plugin does not generate polished UI templates in v1.
- The plugin does not decide visual design direction.
- The plugin does not automate a browser; it serves local pages and receives
  browser-originated events.
- The plugin does not install Node or other runtime dependencies.

## Session safety and verification

Each published screen has a distinct identity. The bundled browser helper
attaches it to events. The server rejects old-screen events and duplicate event
IDs, and timestamps accepted events itself. Reads and waits select the current
screen. Event and shutdown requests require the session token; cross-origin
requests and unexpected Host headers are rejected.

`stop` authenticates to the session server. It never signals a saved PID, so a
stale PID file cannot terminate an unrelated process. An unreachable session
without a shutdown marker returns blocked. Shutdown closes browser sockets and
removes the active session markers. The private token file is readable only by
its owner on POSIX systems. Screens are trusted agent-authored HTML with access
to the browser token. Do not publish untrusted HTML or expose this local server
to an untrusted network.

To run the real Node and Chromium check, install Playwright and its Chromium
browser in a development environment, then run
`python scripts/verify_visual_companion_browser.py`. The check requires both
dependencies and fails if either is unavailable. It makes no model calls.
