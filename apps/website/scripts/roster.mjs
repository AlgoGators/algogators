// The roster on who-we-are is the single source of truth for every headcount
// on the site. Counts used to be typed by hand in five places and drifted:
// the site claimed 26 while what-we-do claimed 34 and the page rendered 24.
import { readFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'site');
const ROSTER = 'who-we-are.html';

/** Names in document order, per roster section, duplicates preserved. */
export function readRoster(siteDir = SITE) {
  const html = readFileSync(join(siteDir, ROSTER), 'utf8');
  const sections = [];
  // A section runs from its <h2> until the next one; names are the .nm tiles.
  for (const chunk of html.split(/<h2>/).slice(1)) {
    const title = chunk.slice(0, chunk.indexOf('</h2>')).trim();
    const names = [...chunk.matchAll(/<p class="nm">([^<]+)<\/p>/g)].map((m) => m[1].trim());
    sections.push({ title, names });
  }
  return sections;
}

/**
 * Distinct people, not tiles. Team leads appear twice by design — once under
 * Leadership and once under the team they run — so counting tiles overstates
 * the roster by the number of leads.
 */
export function memberCount(sections) {
  return new Set(sections.flatMap((s) => s.names)).size;
}

/** Per-section tile counts, in page order. */
export function sectionCounts(sections) {
  return sections.map((s) => s.names.length);
}

export const pad2 = (n) => String(n).padStart(2, '0');
