#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

function usage() {
  process.stderr.write(`Usage:
  node wait-for-event.cjs <state-dir> [--timeout-ms N|0|none] [--since-ms N] [--type TYPE] [--clear]

Waits for the next browser event recorded by the visual companion server and
prints the matching JSON event to stdout.

--clear ignores stale events from before this wait, while preserving very
recent clicks that may have happened just before the command started.
--timeout-ms 0 or --timeout-ms none waits indefinitely.
`);
}

function parseArgs(argv) {
  if (argv.includes('--help') || argv.includes('-h')) {
    return { help: true };
  }

  const args = {
    stateDir: argv[0],
    timeoutMs: 10 * 60 * 1000,
    sinceMs: 0,
    type: null,
    clear: false,
    clearGraceMs: 5000,
    keepalive: true,
  };

  for (let index = 1; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === '--timeout-ms') {
      args.timeoutMs = value === 'none' ? 0 : Number(value);
      index += 1;
    } else if (key === '--since-ms') {
      args.sinceMs = Number(value);
      index += 1;
    } else if (key === '--type') {
      args.type = value;
      index += 1;
    } else if (key === '--clear') {
      args.clear = true;
    } else if (key === '--clear-grace-ms') {
      args.clearGraceMs = Number(value);
      index += 1;
    } else if (key === '--no-keepalive') {
      args.keepalive = false;
    } else {
      throw new Error(`Unknown argument: ${key}`);
    }
  }

  return args;
}

function readEvents(eventsFile, args) {
  if (!fs.existsSync(eventsFile)) return [];

  const markerPath = path.join(path.dirname(eventsFile), 'pending-question.json');
  let screenId = null;
  try { screenId = JSON.parse(fs.readFileSync(markerPath, 'utf8')).screenId; } catch {}
  const seenEventIds = new Set();
  return fs.readFileSync(eventsFile, 'utf8')
    .split(/\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .filter((event) => !screenId || event.screenId === screenId)
    .filter((event) => !args.type || event.type === args.type)
    .filter((event) => Number(event.timestamp || 0) >= args.sinceMs)
    .filter((event) => {
      if (!event.eventId) return true;
      if (seenEventIds.has(event.eventId)) return false;
      seenEventIds.add(event.eventId);
      return true;
    });
}

function readServerInfo(stateDir) {
  const infoFile = path.join(stateDir, 'server-info');
  if (!fs.existsSync(infoFile)) return null;

  try {
    return JSON.parse(fs.readFileSync(infoFile, 'utf8'));
  } catch {
    return null;
  }
}

function pingServer(url) {
  if (!url || typeof fetch !== 'function') return;
  fetch(`${url.replace(/\/$/, '')}/__visual_companion_wait_ping`, { cache: 'no-store' })
    .catch(() => {});
}

function appendDiagnostic(stateDir, action, detail = {}) {
  const entry = {
    action,
    timestamp: Date.now(),
    ...detail,
  };
  try {
    fs.appendFileSync(
      path.join(stateDir, 'diagnostics.jsonl'),
      JSON.stringify(entry) + '\n'
    );
  } catch {
    // Diagnostics must never break the bridge.
  }
}

function writePendingMarker(stateDir, args) {
  let screenId = null;
  try { screenId = JSON.parse(fs.readFileSync(path.join(stateDir, 'pending-question.json'), 'utf8')).screenId; } catch {}
  const pending = {
    screenId,
    id: `automatic-bridge-${Date.now()}`,
    status: 'pending',
    type: args.type,
    createdAt: Date.now(),
    sinceMs: args.sinceMs,
    source: 'wait-for-event',
  };
  fs.writeFileSync(
    path.join(stateDir, 'pending-question.json'),
    `${JSON.stringify(pending, null, 2)}\n`
  );
  return pending;
}

function writeAnsweredMarker(stateDir, pending, event) {
  const answeredAt = Date.now();
  const answered = {
    status: 'answered',
    pending,
    event,
    answeredAt,
  };
  fs.writeFileSync(
    path.join(stateDir, 'answered-question.json'),
    `${JSON.stringify(answered, null, 2)}\n`
  );
  fs.writeFileSync(
    path.join(stateDir, 'pending-question.json'),
    `${JSON.stringify({ ...pending, status: 'answered', answeredAt }, null, 2)}\n`
  );
}

async function waitForEvent(args) {
  if (!args.stateDir || args.help) {
    usage();
    return args.help ? 0 : 2;
  }
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs < 0) {
    throw new Error('--timeout-ms must be a non-negative number');
  }
  if (!Number.isFinite(args.sinceMs) || args.sinceMs < 0) {
    throw new Error('--since-ms must be a non-negative number');
  }
  if (!Number.isFinite(args.clearGraceMs) || args.clearGraceMs < 0) {
    throw new Error('--clear-grace-ms must be a non-negative number');
  }

  const stateDir = path.resolve(args.stateDir);
  const eventsFile = path.join(stateDir, 'events');
  if (!fs.existsSync(stateDir)) {
    throw new Error(`State directory does not exist: ${stateDir}`);
  }
  if (args.clear) {
    args.sinceMs = Math.max(args.sinceMs, Date.now() - args.clearGraceMs);
  }
  const pending = writePendingMarker(stateDir, args);
  appendDiagnostic(stateDir, 'wait-started', {
    pendingId: pending.id,
    type: args.type,
    sinceMs: args.sinceMs,
    timeoutMs: args.timeoutMs,
  });

  const existing = readEvents(eventsFile, args);
  if (existing.length > 0) {
    const event = existing[existing.length - 1];
    writeAnsweredMarker(stateDir, pending, event);
    appendDiagnostic(stateDir, 'wait-matched-existing-event', {
      pendingId: pending.id,
      eventId: event.eventId || null,
      type: event.type || null,
      choice: event.choice || null,
    });
    process.stdout.write(`${JSON.stringify(event, null, 2)}\n`);
    return 0;
  }

  const startedAt = Date.now();
  let watcher = null;
  const serverInfo = args.keepalive ? readServerInfo(stateDir) : null;
  if (serverInfo && serverInfo.url) {
    pingServer(serverInfo.url);
  }

  return await new Promise((resolve, reject) => {
    const check = () => {
      if (fs.existsSync(path.join(stateDir, 'server-stopped'))) {
        cleanup();
        appendDiagnostic(stateDir, 'wait-server-stopped', { pendingId: pending.id });
        reject(new Error('Visual companion server stopped while waiting for browser event'));
        return;
      }
      const events = readEvents(eventsFile, args);
      if (events.length > 0) {
        const event = events[events.length - 1];
        cleanup();
        writeAnsweredMarker(stateDir, pending, event);
        appendDiagnostic(stateDir, 'wait-matched-event', {
          pendingId: pending.id,
          eventId: event.eventId || null,
          type: event.type || null,
          choice: event.choice || null,
        });
        process.stdout.write(`${JSON.stringify(event, null, 2)}\n`);
        resolve(0);
        return;
      }
      if (args.timeoutMs > 0 && Date.now() - startedAt > args.timeoutMs) {
        cleanup();
        appendDiagnostic(stateDir, 'wait-timed-out', {
          pendingId: pending.id,
          timeoutMs: args.timeoutMs,
        });
        reject(new Error(`Timed out waiting for browser event after ${args.timeoutMs}ms`));
      }
    };

    const cleanup = () => {
      clearInterval(interval);
      clearInterval(keepaliveInterval);
      if (watcher) watcher.close();
    };

    const interval = setInterval(check, 200);
    const keepaliveInterval = setInterval(() => {
      if (serverInfo && serverInfo.url) pingServer(serverInfo.url);
    }, 25 * 1000);
    keepaliveInterval.unref();
    watcher = fs.watch(stateDir, check);
    check();
  });
}

let args;
try {
  args = parseArgs(process.argv.slice(2));
  waitForEvent(args)
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    });
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  usage();
  process.exitCode = 2;
}
