// Build = copy site/ to dist/, then stamp every headcount from the roster.
// The site is hand-authored static HTML; the "build" exists so the member
// answers the same four gates as every other node member and so publish
// workflows have a dist/ to ship.
import { cpSync, rmSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readRoster, memberCount, sectionCounts } from './roster.mjs';
import { readApplications } from './applications.mjs';
import { stamp } from './stamp.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SITE = resolve(ROOT, 'site');
const DIST = resolve(ROOT, 'dist');

rmSync(DIST, { recursive: true, force: true });
mkdirSync(DIST, { recursive: true });
cpSync(SITE, DIST, { recursive: true });
console.log('site/ copied to dist/');

// The numbers checked into site/ are a readable fallback for anyone opening
// the files directly; dist/ is always stamped from the roster itself.
const sections = readRoster(SITE);
const total = memberCount(sections);
const perSection = sectionCounts(sections);
const applications = readApplications(SITE);

for (const page of readdirSync(DIST).filter((f) => f.endsWith('.html'))) {
  const file = join(DIST, page);
  const before = readFileSync(file, 'utf8');

  const after = stamp(before, { total, perSection, applications });

  if (after !== before) {
    writeFileSync(file, after);
    console.log(`  stamped ${page}`);
  }
}

console.log(`roster: ${total} distinct members · sections ${perSection.join('/')}`);
console.log(`applications: ${applications.term} · closes ${applications.closesLabel}`);
