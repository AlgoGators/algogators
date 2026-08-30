// The application window is configured once, in site/assets/main.js, because
// the browser needs it to decide which copy to show. Reading it back here lets
// the build stamp the same values into the markup, so a new intake means
// editing three adjacent lines and nothing else.
import { readFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'site');

/** { term, closes, closesLabel } as declared in main.js. */
export function readApplications(siteDir = SITE) {
  const js = readFileSync(join(siteDir, 'assets', 'main.js'), 'utf8');
  const block = js.match(/var APPLICATIONS = \{([\s\S]*?)\};/);
  if (!block) throw new Error('main.js: APPLICATIONS config not found');

  const field = (name) => {
    const m = block[1].match(new RegExp(`${name}:\\s*'([^']*)'`));
    if (!m) throw new Error(`main.js: APPLICATIONS.${name} not found`);
    return m[1];
  };
  return { term: field('term'), closes: field('closes'), closesLabel: field('closesLabel') };
}
