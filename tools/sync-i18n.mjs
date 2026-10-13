// Rebuild Server's reviewed offline translations. No network/API translation,
// and no writes to other repositories unless explicitly requested.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await import('./build-i18n.mjs');
// Cloud och TKL bär samma körtid och katalog (src/i18n) och samma ritare för
// tågdiagrammet (src/graph): drift-model.js och train-graph.js, oförändrade.
export const SHARED = [['i18n.js', 'src/i18n/core.js'], ['i18n-messages.js', 'src/i18n/messages.js'],
  ['drift-model.js', 'src/graph/drift-model.js'], ['train-graph.js', 'src/graph/train-graph.js']];
for (const project of process.argv.includes('--siblings') ? ['trainmeet-cloud', 'trainmeet-tkl'] : []) {
  for (const [source, target] of SHARED) {
    const dest = path.join(repo, '..', project, target);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(path.join(repo, 'src/tmbox_gateway/web', source), dest);
  }
}
