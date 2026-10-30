const fs = require('fs');
const path = require('path');

module.exports = function currentScreen(stateDir) {
  const contentDir = path.join(stateDir, '..', 'content');
  if (!fs.existsSync(contentDir)) return null;
  const files = fs.readdirSync(contentDir)
    .filter(name => name.endsWith('.html'))
    .map(name => ({ name, modified: fs.statSync(path.join(contentDir, name)).mtimeMs }))
    .sort((a, b) => b.modified - a.modified || b.name.localeCompare(a.name));
  return files.length ? files[0].name : null;
};
