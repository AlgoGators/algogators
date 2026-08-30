// Rewrites the numbers and dates checked into site/ from their single sources,
// so adding an analyst or changing the intake window is one edit plus this.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readRoster, memberCount, sectionCounts } from './roster.mjs';
import { readApplications } from './applications.mjs';
import { stampPreservingCounters } from './stamp.mjs';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'site');
const sections = readRoster(SITE);
const values = {
  total: memberCount(sections),
  perSection: sectionCounts(sections),
  applications: readApplications(SITE),
};

let changed = 0;
for (const page of readdirSync(SITE).filter((f) => f.endsWith('.html'))) {
  const file = join(SITE, page);
  const before = readFileSync(file, 'utf8');
  const after = stampPreservingCounters(before, values);
  if (after !== before) {
    writeFileSync(file, after);
    console.log(`  updated ${page}`);
    changed += 1;
  }
}
console.log(
  changed
    ? `synced ${changed} page(s): ${values.total} members · sections ${values.perSection.join('/')}`
    : `already in sync: ${values.total} members · sections ${values.perSection.join('/')}`,
);
