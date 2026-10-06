// Copy the timetable core from TrainMeet Cloud, which owns it. The copy must be
// byte-identical: tests/test_timetable_core.py locks it with the same digest as
// Cloud's own test. Run from this repository with trainmeet-cloud next to it.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(repo, '..', 'trainmeet-cloud', 'cloud', 'timetable_core');
const dest = path.join(repo, 'src', 'tmbox_gateway', 'timetable_core');
const modules = dir => fs.readdirSync(dir).filter(name => name.endsWith('.py')).sort();
if (!fs.existsSync(source)) throw new Error(`Hittar inte ${source}`);
fs.mkdirSync(dest, { recursive: true });
// A module Cloud has removed must go here too, or the digests differ.
for (const name of modules(dest)) if (!modules(source).includes(name)) fs.rmSync(path.join(dest, name));
for (const name of modules(source)) fs.copyFileSync(path.join(source, name), path.join(dest, name));
console.log(`timetable_core: ${modules(dest).join(', ')}`);
