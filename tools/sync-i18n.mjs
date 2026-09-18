// Run from any directory. The three deliverables remain independently installable.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const project of ['trainmeet-cloud', 'trainmeet-tkl']) {
  const dest = path.join(repo, '..', project, 'src/i18n');
  fs.mkdirSync(dest, { recursive: true });
  for (const [source, target] of [['i18n.js', 'core.js'], ['i18n-messages.js', 'messages.js']]) {
    fs.copyFileSync(path.join(repo, 'src/tmbox_gateway/web', source), path.join(dest, target));
  }
}
