const fs = require('fs');
const path = require('path');
const http = require('http');

async function main() {
  if (!process.argv[2]) throw new Error('Usage: stop-server.sh <session-dir>');
  const stateDir = path.join(path.resolve(process.argv[2]), 'state');
  const infoPath = path.join(stateDir, 'server-info');
  if (!fs.existsSync(infoPath)) {
    process.stdout.write(JSON.stringify({ status: 'not_running' }) + '\n');
    return;
  }
  const info = JSON.parse(fs.readFileSync(infoPath, 'utf8'));
  const token = fs.readFileSync(path.join(stateDir, 'session-token'), 'utf8').trim();
  await new Promise((resolve, reject) => {
    const request = http.request(info.url + '/__visual_companion_stop', {
      method: 'POST', headers: { 'x-visual-companion-token': token }, timeout: 5000,
    }, response => {
      response.resume();
      if (response.statusCode === 202) resolve();
      else reject(new Error('Session refused shutdown'));
    });
    request.on('error', reject);
    request.on('timeout', () => request.destroy(new Error('Shutdown timed out')));
    request.end();
  });
  const deadline = Date.now() + 5000;
  while (fs.existsSync(infoPath) && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  if (fs.existsSync(infoPath)) throw new Error('Server did not confirm shutdown');
  process.stdout.write(JSON.stringify({ status: 'stopped' }) + '\n');
}

main().catch(error => {
  process.stderr.write(error.message + '\n');
  process.exitCode = 1;
});
