#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

function usage() {
  process.stderr.write(`Usage:
  node mark-pending-question.cjs <state-dir> [--id ID] [--type TYPE] [--grace-ms N]

Records a durable pending visual question marker. Use this before returning to
other work so a later check can recover the browser click without keeping a
blocking wait command open.
`);
}

function parseArgs(argv) {
  if (argv.includes('--help') || argv.includes('-h')) return { help: true };
  const args = {
    stateDir: argv[0],
    id: `visual-question-${Date.now()}`,
    type: null,
    graceMs: 5000,
  };

  for (let index = 1; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === '--id') {
      args.id = value;
      index += 1;
    } else if (key === '--type') {
      args.type = value;
      index += 1;
    } else if (key === '--grace-ms') {
      args.graceMs = Number(value);
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${key}`);
    }
  }

  return args;
}

function main(args) {
  if (!args.stateDir || args.help) {
    usage();
    return args.help ? 0 : 2;
  }
  if (!Number.isFinite(args.graceMs) || args.graceMs < 0) {
    throw new Error('--grace-ms must be a non-negative number');
  }

  const stateDir = path.resolve(args.stateDir);
  if (!fs.existsSync(stateDir)) {
    throw new Error(`State directory does not exist: ${stateDir}`);
  }

  const now = Date.now();
  const pending = {
    id: args.id,
    status: 'pending',
    type: args.type,
    createdAt: now,
    sinceMs: now - args.graceMs,
  };

  fs.writeFileSync(
    path.join(stateDir, 'pending-question.json'),
    `${JSON.stringify(pending, null, 2)}\n`
  );
  fs.appendFileSync(
    path.join(stateDir, 'diagnostics.jsonl'),
    JSON.stringify({
      action: 'pending-marked',
      timestamp: now,
      pendingId: pending.id,
      type: pending.type,
      sinceMs: pending.sinceMs,
    }) + '\n'
  );
  process.stdout.write(`${JSON.stringify(pending, null, 2)}\n`);
  return 0;
}

try {
  process.exitCode = main(parseArgs(process.argv.slice(2)));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  usage();
  process.exitCode = 2;
}
