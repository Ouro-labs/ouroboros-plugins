#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

function usage() {
  process.stderr.write(`Usage:
  node read-pending-event.cjs <state-dir> [--type TYPE] [--since-ms N]

Checks durable visual companion events without blocking. If a pending question
exists, its cutoff is used automatically. Prints answered/pending JSON.
`);
}

function parseArgs(argv) {
  if (argv.includes('--help') || argv.includes('-h')) return { help: true };
  const args = {
    stateDir: argv[0],
    type: null,
    sinceMs: null,
  };

  for (let index = 1; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === '--type') {
      args.type = value;
      index += 1;
    } else if (key === '--since-ms') {
      args.sinceMs = Number(value);
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${key}`);
    }
  }

  return args;
}

function readJson(file) {
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function readEvents(eventsFile, type, sinceMs) {
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
    .filter((event) => event.type !== 'heartbeat')
    .filter((event) => !type || event.type === type)
    .filter((event) => Number(event.timestamp || 0) >= sinceMs)
    .filter((event) => {
      if (!event.eventId) return true;
      if (seenEventIds.has(event.eventId)) return false;
      seenEventIds.add(event.eventId);
      return true;
    });
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
    // Diagnostics must never block recovery reads.
  }
}

function main(args) {
  if (!args.stateDir || args.help) {
    usage();
    return args.help ? 0 : 2;
  }
  if (args.sinceMs !== null && (!Number.isFinite(args.sinceMs) || args.sinceMs < 0)) {
    throw new Error('--since-ms must be a non-negative number');
  }

  const stateDir = path.resolve(args.stateDir);
  if (!fs.existsSync(stateDir)) {
    throw new Error(`State directory does not exist: ${stateDir}`);
  }

  const pendingFile = path.join(stateDir, 'pending-question.json');
  const pending = readJson(pendingFile);
  const type = args.type || (pending && pending.type) || null;
  const sinceMs = args.sinceMs !== null
    ? args.sinceMs
    : Number((pending && pending.sinceMs) || 0);

  const events = readEvents(path.join(stateDir, 'events'), type, sinceMs);
  if (events.length === 0) {
    appendDiagnostic(stateDir, 'pending-read-no-match', {
      pendingId: pending && pending.id,
      type,
      sinceMs,
    });
    process.stdout.write(`${JSON.stringify({ status: 'pending', pending }, null, 2)}\n`);
    return 0;
  }

  const event = events[events.length - 1];
  const answered = {
    status: 'answered',
    pending,
    event,
    answeredAt: Date.now(),
  };
  fs.writeFileSync(
    path.join(stateDir, 'answered-question.json'),
    `${JSON.stringify(answered, null, 2)}\n`
  );
  if (pending) {
    fs.writeFileSync(
      pendingFile,
      `${JSON.stringify({ ...pending, status: 'answered', answeredAt: answered.answeredAt }, null, 2)}\n`
    );
  }
  appendDiagnostic(stateDir, 'pending-read-answered', {
    pendingId: pending && pending.id,
    eventId: event.eventId || null,
    type: event.type || null,
    choice: event.choice || null,
  });
  process.stdout.write(`${JSON.stringify(answered, null, 2)}\n`);
  return 0;
}

try {
  process.exitCode = main(parseArgs(process.argv.slice(2)));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  usage();
  process.exitCode = 2;
}
