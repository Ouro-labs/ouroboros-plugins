---
name: visual-companion
description: Use when a user wants a standalone browser-based visual companion for interactive mockups, diagrams, wireframes, layout comparisons, visual brainstorming, or clickable visual option selection. Starts a local zero-dependency Node server, writes HTML screens, and reads browser click events.
---

# Visual Companion

This is a standalone skill. It is not tied to the broader brainstorming workflow.

Resolve bundled scripts relative to this skill directory:

- `scripts/start-server.sh`
- `scripts/stop-server.sh`
- `scripts/server.cjs`
- `scripts/frame-template.html`
- `scripts/helper.js`
- `scripts/wait-for-event.cjs`
- `scripts/mark-pending-question.cjs`
- `scripts/read-pending-event.cjs`

Browser-based visual brainstorming companion for showing mockups, diagrams, and options.

## When to Use

Decide per-question, not per-session. The test: **would the user understand this better by seeing it than reading it?**

**Use the browser** when the content itself is visual:

- **UI mockups** — wireframes, layouts, navigation structures, component designs
- **Architecture diagrams** — system components, data flow, relationship maps
- **Side-by-side visual comparisons** — comparing two layouts, two color schemes, two design directions
- **Design polish** — when the question is about look and feel, spacing, visual hierarchy
- **Spatial relationships** — state machines, flowcharts, entity relationships rendered as diagrams

**Use the terminal** when the content is text or tabular:

- **Requirements and scope questions** — "what does X mean?", "which features are in scope?"
- **Conceptual A/B/C choices** — picking between approaches described in words
- **Tradeoff lists** — pros/cons, comparison tables
- **Technical decisions** — API design, data modeling, architectural approach selection
- **Clarifying questions** — anything where the answer is words, not a visual preference

A question *about* a UI topic is not automatically a visual question. "What kind of wizard do you want?" is conceptual — use the terminal. "Which of these wizard layouts feels right?" is visual — use the browser.

## How It Works

The server watches a directory for HTML files and serves the newest one to the browser. You write HTML content to `screen_dir`, the user sees it in their browser and can click to select options or submit small forms. Browser events are recorded to `state_dir/events`, `state_dir/latest-event.json`, and `state_dir/latest-selection.json`.

Browser events are durable. Do not depend only on a currently running `wait-for-event.cjs` process. The browser sends user events through WebSocket and an immediate HTTP fallback, and the server deduplicates them by `eventId`. If the waiting command is interrupted or the user may ask unrelated terminal questions while a visual choice remains open, recover the answer later with `read-pending-event.cjs`.

Diagnostics are durable too. Browser helper sends, server records, duplicate drops, wait starts, wait matches, timeout events, and pending recovery checks are appended as JSON lines to `state_dir/diagnostics.jsonl`. Check that file first when a user says they clicked but Codex did not react.

The server injects a per-session token into served pages and requires that token for browser event, diagnostic, and WebSocket event paths. This prevents unrelated local pages from spoofing visual choices when the server port is reachable.

For the best Codex/Claude flow, do not require the user to retype a browser choice in the terminal. After pushing a screen, run `scripts/wait-for-event.cjs` with the returned `state_dir`; it blocks until the next browser event and prints the JSON event directly into the session.

**Content fragments vs full documents:** If your HTML file starts with `<!DOCTYPE` or `<html`, the server serves it as-is (just injects the helper script). Otherwise, the server automatically wraps your content in the frame template — adding the header, CSS theme, selection indicator, and all interactive infrastructure. **Write content fragments by default.** Only write full documents when you need complete control over the page.

## Starting a Session

```bash
# Start server with persistence (mockups saved to project)
scripts/start-server.sh --project-dir /path/to/project

# Returns: {"type":"server-started","port":52341,"url":"http://localhost:52341",
#           "screen_dir":"/path/to/project/.brainstorm/12345-1706000000/content",
#           "state_dir":"/path/to/project/.brainstorm/12345-1706000000/state"}
```

Save `screen_dir` and `state_dir` from the response. Tell user to open the URL.

**Finding connection info:** The server writes its startup JSON to `$STATE_DIR/server-info`. If you launched the server in the background and didn't capture stdout, read that file to get the URL and port. When using `--project-dir`, check `<project>/.brainstorm/` for the session directory.

**Note:** Pass the project root as `--project-dir` so mockups persist in `.brainstorm/` and survive server restarts. Without it, files go to `/tmp` and get cleaned up. Remind the user to add `.brainstorm/` to `.gitignore` if it's not already there.

**Launching the server by platform:**

**Claude Code (macOS / Linux):**
```bash
# Default mode works — the script backgrounds the server itself
scripts/start-server.sh --project-dir /path/to/project
```

**Claude Code (Windows):**
```bash
# Windows auto-detects and uses foreground mode, which blocks the tool call.
# Use run_in_background: true on the Bash tool call so the server survives
# across conversation turns.
scripts/start-server.sh --project-dir /path/to/project
```
When calling this via the Bash tool, set `run_in_background: true`. Then read `$STATE_DIR/server-info` on the next turn to get the URL and port.

**Codex:**
```bash
# Codex reaps background processes. The script auto-detects CODEX_CI and
# switches to foreground mode. Run it normally — no extra flags needed.
scripts/start-server.sh --project-dir /path/to/project
```

**Gemini CLI:**
```bash
# Use --foreground and set is_background: true on your shell tool call
# so the process survives across turns
scripts/start-server.sh --project-dir /path/to/project --foreground
```

**Other environments:** The server must keep running in the background across conversation turns. If your environment reaps detached processes, use `--foreground` and launch the command with your platform's background execution mechanism.

If the URL is unreachable from your browser (common in remote/containerized setups), bind a non-loopback host:

```bash
scripts/start-server.sh \
  --project-dir /path/to/project \
  --host 0.0.0.0 \
  --url-host localhost
```

Use `--url-host` to control what hostname is printed in the returned URL JSON.

## The Loop

0. **Check pending visual answers first** — at the start of any later user turn, if a visual companion session is active or a previous visual question was left pending, run:

   ```bash
   node scripts/read-pending-event.cjs "$STATE_DIR"
   ```

   If it returns `answered`, use that browser choice before continuing with the new request. Do this proactively; do not wait for the user to remind you to check. If it returns `pending`, continue with the new request and leave the visual question open.

1. **Check server is alive**, then **write HTML** to a new file in `screen_dir`:
      - Before each write, check that `$STATE_DIR/server-info` exists. If it doesn't (or `$STATE_DIR/server-stopped` exists), the server has shut down — restart it with `start-server.sh` before continuing. The server auto-exits after 4 hours of inactivity. An open browser page sends heartbeat events to keep active sessions alive.
   - Use semantic filenames: `platform.html`, `visual-style.html`, `layout.html`
   - **Never reuse filenames** — each screen gets a fresh file
   - Use Write tool — **never use cat/heredoc** (dumps noise into terminal)
   - Server automatically serves the newest file

2. **Choose a response mode:**
   - **Automatic bridge, preferred:** run `node scripts/wait-for-event.cjs "$STATE_DIR" --clear --timeout-ms 1800000` after writing the screen. Use `--timeout-ms 0` only when an intentionally open-ended wait is acceptable. The user clicks or submits in the browser, and the command returns the event JSON in the same session. Use this when the next step depends only on the browser interaction. The bridge keeps the local server alive while it waits, creates a pending recovery marker, and `--clear` ignores stale events without deleting clicks that arrive just before the wait command starts.
   - **Durable pending mode:** run `node scripts/mark-pending-question.cjs "$STATE_DIR" --id <short-id>` after writing the screen, then continue with other work or end the turn. Later, run `node scripts/read-pending-event.cjs "$STATE_DIR"` to recover the latest matching click without blocking. Use this whenever the user may keep using the terminal for other prompts while the visual question remains open.
   - **Terminal confirmation:** end your turn and ask the user to respond in terminal only when their feedback is primarily prose, the browser is optional, or long discussion is expected.

3. **Tell user what to expect:**
   - Remind them of the URL (every step, not just first)
   - Give a brief text summary of what's on screen (e.g., "Showing 3 layout options for the homepage")
   - For automatic bridge mode, say: "Click or submit in the browser; I will receive it here automatically."
   - For terminal confirmation mode, say: "Take a look and let me know what you think. Click to select an option if you'd like."

4. **Read the result:**
   - In automatic bridge mode, parse the JSON printed by `wait-for-event.cjs`.
   - In terminal confirmation mode, after the user responds in the terminal:
   - Read `$STATE_DIR/events` if it exists — this contains the user's browser interactions (clicks, selections) as JSON lines
   - Merge with the user's terminal text to get the full picture
   - The terminal message is the primary feedback; `state_dir/events` provides structured interaction data

5. **Iterate or advance** — if feedback changes current screen, write a new file (e.g., `layout-v2.html`). Only move to the next question when the current step is validated.

6. **Unload when returning to terminal** — when the next step doesn't need the browser (e.g., a clarifying question, a tradeoff discussion), push a waiting screen to clear the stale content:

   ```html
   <!-- filename: waiting.html (or waiting-2.html, etc.) -->
   <div style="display:flex;align-items:center;justify-content:center;min-height:60vh">
     <p class="subtitle">Continuing in terminal...</p>
   </div>
   ```

   This prevents the user from staring at a resolved choice while the conversation has moved on. When the next visual question comes up, push a new content file as usual.

7. Repeat until done.

## Automatic Browser-to-Session Bridge

Use this when a browser click or short form submit should drive the next agent step without asking the user to type the same answer again.

```bash
node .codex/skills/visual-companion/scripts/wait-for-event.cjs "$STATE_DIR" --clear --timeout-ms 1800000
```

The command prints one JSON event and exits. Useful fields:

- `type`: normally `click`, `choice`, or `submit`.
- `choice`: the selected `data-choice`, when present.
- `value`: explicit `data-value` or API value, when present.
- `fields`: submitted form fields, when present.
- `text`: visible text from the clicked or submitted element.

Use `--type submit` to wait only for form submissions. Use `--since-ms <timestamp>` when you want to preserve older events and wait for a newer one.

Reliability guards:

- `--clear` is race-safe. It sets a cutoff for old events instead of deleting the events file, so a fast click made between screen render and waiter startup is still accepted.
- `--clear-grace-ms <N>` controls that fast-click grace window. The default is `5000`.
- `--timeout-ms 0` disables the bridge timeout for deliberately long waits.
- While waiting, the command pings the local server every 25 seconds so long user pauses do not trigger the server idle timeout. Use `--no-keepalive` only for debugging.
- The waiting command writes `pending-question.json` before blocking and marks it answered after a matching event. If the shell wait is interrupted, run `read-pending-event.cjs` later to recover the click.
- Browser clicks are sent by WebSocket and by HTTP fallback. Duplicate deliveries are expected and are collapsed by `eventId`.
- To debug missed clicks, inspect `state_dir/diagnostics.jsonl` alongside `state_dir/events`.

This bridge does not inject messages into Codex or Claude through a private UI API. It returns the browser event through the same shell/tool channel the agent already controls, which is the reliable cross-runtime path.

## Durable Pending Questions

Use this when the browser question should remain open while the user may continue asking unrelated terminal questions.

After writing the screen:

```bash
node .codex/skills/visual-companion/scripts/mark-pending-question.cjs "$STATE_DIR" --id layout-choice
```

Then continue with the other requested work. The server keeps recording browser events to disk.

At the start of each later user turn, before responding to the new request:

```bash
node .codex/skills/visual-companion/scripts/read-pending-event.cjs "$STATE_DIR"
```

Return shapes:

- `{"status":"pending", ...}` means no matching browser answer has arrived yet.
- `{"status":"answered", "event": ...}` means use `event.choice`, `event.value`, `event.fields`, or `event.text`.

This mode is the fallback guard for long waits, interrupted turns, and terminal-side detours.

## Writing Content Fragments

Write just the content that goes inside the page. The server wraps it in the frame template automatically (header, theme CSS, selection indicator, and all interactive infrastructure).

**Minimal example:**

```html
<h2>Which layout works better?</h2>
<p class="subtitle">Consider readability and visual hierarchy</p>

<div class="options">
  <div class="option" data-choice="a" onclick="toggleSelect(this)">
    <div class="letter">A</div>
    <div class="content">
      <h3>Single Column</h3>
      <p>Clean, focused reading experience</p>
    </div>
  </div>
  <div class="option" data-choice="b" onclick="toggleSelect(this)">
    <div class="letter">B</div>
    <div class="content">
      <h3>Two Column</h3>
      <p>Sidebar navigation with main content</p>
    </div>
  </div>
</div>
```

That's it. No `<html>`, no CSS, no `<script>` tags needed. The server provides all of that.

## CSS Classes Available

The frame template provides these CSS classes for your content:

### Options (A/B/C choices)

```html
<div class="options">
  <div class="option" data-choice="a" onclick="toggleSelect(this)">
    <div class="letter">A</div>
    <div class="content">
      <h3>Title</h3>
      <p>Description</p>
    </div>
  </div>
</div>
```

**Multi-select:** Add `data-multiselect` to the container to let users select multiple options. Each click toggles the item. The indicator bar shows the count.

```html
<div class="options" data-multiselect>
  <!-- same option markup — users can select/deselect multiple -->
</div>
```

### Cards (visual designs)

```html
<div class="cards">
  <div class="card" data-choice="design1" onclick="toggleSelect(this)">
    <div class="card-image"><!-- mockup content --></div>
    <div class="card-body">
      <h3>Name</h3>
      <p>Description</p>
    </div>
  </div>
</div>
```

### Mockup container

```html
<div class="mockup">
  <div class="mockup-header">Preview: Dashboard Layout</div>
  <div class="mockup-body"><!-- your mockup HTML --></div>
</div>
```

### Split view (side-by-side)

```html
<div class="split">
  <div class="mockup"><!-- left --></div>
  <div class="mockup"><!-- right --></div>
</div>
```

### Pros/Cons

```html
<div class="pros-cons">
  <div class="pros"><h4>Pros</h4><ul><li>Benefit</li></ul></div>
  <div class="cons"><h4>Cons</h4><ul><li>Drawback</li></ul></div>
</div>
```

### Mock elements (wireframe building blocks)

```html
<div class="mock-nav">Logo | Home | About | Contact</div>
<div style="display: flex;">
  <div class="mock-sidebar">Navigation</div>
  <div class="mock-content">Main content area</div>
</div>
<button class="mock-button">Action Button</button>
<input class="mock-input" placeholder="Input field">
<textarea class="mock-textarea" placeholder="Text input"></textarea>
<div class="placeholder">Placeholder area</div>
```

### Browser form inputs

Use `data-brainstorm-form` for short free-text answers that should come back through the automatic bridge.

```html
<form data-brainstorm-form data-choice="custom-scope">
  <div class="form-row">
    <label for="scope">Scope notes</label>
    <textarea id="scope" name="scope" class="mock-textarea"></textarea>
  </div>
  <button class="mock-button" type="submit">Submit</button>
</form>
```

For a button outside a form, add `data-submit`. The helper collects nearby `input`, `textarea`, and `select` values and sends a `submit` event.

### Typography and sections

- `h2` — page title
- `h3` — section heading
- `.subtitle` — secondary text below title
- `.section` — content block with bottom margin
- `.label` — small uppercase label text

## Browser Events Format

When the user clicks options in the browser, their interactions are recorded to `$STATE_DIR/events` (one JSON object per line). The file is cleared automatically when you push a new screen.

```jsonl
{"type":"click","choice":"a","text":"Option A - Simple Layout","timestamp":1706000101}
{"type":"click","choice":"c","text":"Option C - Complex Grid","timestamp":1706000108}
{"type":"click","choice":"b","text":"Option B - Hybrid","timestamp":1706000115}
{"type":"submit","choice":"custom","fields":{"notes":"Use compact spacing"},"timestamp":1706000120}
```

The full event stream shows the user's exploration path — they may click multiple options before settling. The last `choice` event is typically the final selection, but the pattern of clicks can reveal hesitation or preferences worth asking about.

If `$STATE_DIR/events` doesn't exist, the user didn't interact with the browser — use only their terminal text.

## Design Tips

- **Scale fidelity to the question** — wireframes for layout, polish for polish questions
- **Explain the question on each page** — "Which layout feels more professional?" not just "Pick one"
- **Iterate before advancing** — if feedback changes current screen, write a new version
- **2-4 options max** per screen
- **Use real content when it matters** — for a photography portfolio, use actual images (Unsplash). Placeholder content obscures design issues.
- **Keep mockups simple** — focus on layout and structure, not pixel-perfect design

## File Naming

- Use semantic names: `platform.html`, `visual-style.html`, `layout.html`
- Never reuse filenames — each screen must be a new file
- For iterations: append version suffix like `layout-v2.html`, `layout-v3.html`
- Server serves newest file by modification time

## Cleaning Up

```bash
scripts/stop-server.sh $SESSION_DIR
```

If the session used `--project-dir`, mockup files persist in `.brainstorm/` for later reference. Only `/tmp` sessions get deleted on stop.

## Reference

- Frame template (CSS reference): `scripts/frame-template.html`
- Helper script (client-side): `scripts/helper.js`
