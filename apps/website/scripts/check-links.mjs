// Lint gate: every local href/src in the HTML pages must point at a file
// that exists in site/. The old repo accumulated dead references (renamed
// headshots, removed pages) that nothing caught until a visitor hit a 404.
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'site');

const pages = readdirSync(SITE).filter((f) => f.endsWith('.html'));
const attrRe = /(?:href|src)="([^"]+)"/g;

const problems = [];
for (const page of pages) {
  const html = readFileSync(join(SITE, page), 'utf8');
  for (const [, target] of html.matchAll(attrRe)) {
    if (/^(https?:|mailto:|tel:|#|data:)/.test(target)) continue;
    const clean = target.split('#')[0].split('?')[0].replaceAll('\\', '/');
    if (!clean) continue;
    if (!existsSync(join(SITE, decodeURIComponent(clean)))) {
      problems.push(`${page}: broken local reference "${target}"`);
    }
  }
}

if (problems.length > 0) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log(`checked ${pages.length} pages, no broken local references`);
