// Structural checks for the static site: every page parses as HTML enough to
// have a title, exactly one <h1>, and the shared stylesheet/script wired in.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'site');
const pages = readdirSync(SITE).filter((f) => f.endsWith('.html'));

test('site has pages', () => {
  assert.ok(pages.length >= 5, `expected the six pages, found ${pages.length}`);
});

for (const page of pages) {
  const html = readFileSync(join(SITE, page), 'utf8');

  test(`${page} has a <title>`, () => {
    assert.match(html, /<title>[^<]+<\/title>/);
  });

  test(`${page} links the shared stylesheet`, () => {
    assert.match(html, /href="assets\/main\.css"/);
  });

  test(`${page} declares a language`, () => {
    assert.match(html, /<html[^>]*\blang=/);
  });
}
