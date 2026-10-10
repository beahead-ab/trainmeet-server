// Produktetiketterna bredvid stationsskylten: SERVER, CLOUD, TKL och TMBOX
// ritade som TMBox-displayens 5×7-punkter (Casper, 2026-10-10). Punkterna
// ligger 3 px isär, så de hamnar på hela pixlar även på en vanlig skärm.
// Filerna är svarta punkter; sidorna använder dem som CSS-mask över
// textfärgen, så att de följer ljust och mörkt tema.
//
//   node tools/dot-label.mjs              skriver serverns web/etikett/*.svg
//   node tools/dot-label.mjs --siblings   skriver även Clouds public/etikett/ och TKL:s src/assets/etikett/
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// 5×7 som en teckendisplay (HD44780), med ett V som läses som V.
export const GLYPHS = {
  B: ['11110', '10001', '10001', '11110', '10001', '10001', '11110'],
  C: ['01110', '10001', '10000', '10000', '10000', '10001', '01110'],
  D: ['11100', '10010', '10001', '10001', '10001', '10010', '11100'],
  E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
  K: ['10001', '10010', '10100', '11000', '10100', '10010', '10001'],
  L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
  M: ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
  O: ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
  R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
  S: ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
  T: ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
  U: ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
  V: ['10001', '10001', '10001', '01010', '01010', '01010', '00100'],
  X: ['10001', '10001', '01010', '00100', '01010', '10001', '10001'],
};
export const PITCH = 3;
export const RADIUS = 1.26;
export const LABELS = { server: 'SERVER', cloud: 'CLOUD', tkl: 'TKL', tmbox: 'TMBOX' };

export function dotLabel(word) {
  const columns = word.length * 6 - 1;
  const width = columns * PITCH, height = 7 * PITCH;
  const dots = [];
  [...word].forEach((letter, index) => {
    const glyph = GLYPHS[letter];
    if (!glyph) throw new Error(`Ingen punktbokstav för ${letter}`);
    glyph.forEach((row, y) => [...row].forEach((bit, x) => {
      if (bit === '1') dots.push(`<circle cx="${(index * 6 + x) * PITCH + PITCH / 2}" cy="${y * PITCH + PITCH / 2}" r="${RADIUS}"/>`);
    }));
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${word}"><title>${word}</title>${dots.join('')}</svg>\n`;
}

export function dotLabelSize(word) {
  return { width: (word.length * 6 - 1) * PITCH, height: 7 * PITCH };
}

// Var varje etikett används: servern har SERVER och TMBOX (TMBox-sidorna),
// Cloud och TKL har sina egna.
export const TARGETS = {
  server: ['src/tmbox_gateway/web/etikett'],
  tmbox: ['src/tmbox_gateway/web/etikett'],
  cloud: ['../trainmeet-cloud/public/etikett'],
  tkl: ['../trainmeet-tkl/src/assets/etikett'],
};

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const siblings = process.argv.includes('--siblings');
  const written = [];
  for (const [name, folders] of Object.entries(TARGETS)) {
    for (const folder of folders) {
      if (folder.startsWith('..') && !siblings) continue;
      const dest = path.join(repo, folder);
      fs.mkdirSync(dest, { recursive: true });
      fs.writeFileSync(path.join(dest, `${name}.svg`), dotLabel(LABELS[name]));
      const { width, height } = dotLabelSize(LABELS[name]);
      written.push(`${path.join(folder, name)}.svg ${width}×${height}`);
    }
  }
  console.log(written.join('\n'));
}
