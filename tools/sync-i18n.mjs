// Rebuild Server's reviewed offline translations. No network/API translation,
// and no writes to other repositories unless explicitly requested.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await import('./build-i18n.mjs');
for (const project of process.argv.includes('--siblings') ? ['trainmeet-cloud', 'trainmeet-tkl'] : []) {
  const dest = path.join(repo, '..', project, 'src/i18n');
  fs.mkdirSync(dest, { recursive: true });
  for (const [source, target] of [['i18n.js', 'core.js'], ['i18n-messages.js', 'messages.js']]) {
    fs.copyFileSync(path.join(repo, 'src/tmbox_gateway/web', source), path.join(dest, target));
  }
}
