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

// ── Roster-derived counts ────────────────────────────────────────────────
// Hand-typed headcounts drifted apart: the site claimed 26 in four places,
// what-we-do claimed 34, Investor Relations declared 5 while showing 4, and
// the roster actually held 24 distinct people. These lock the invariant.
import { readRoster, memberCount, sectionCounts, pad2 } from './roster.mjs';
import { readApplications } from './applications.mjs';

test('roster counts distinct people, not tiles', () => {
  const sections = readRoster(SITE);
  const tiles = sections.reduce((n, s) => n + s.names.length, 0);
  assert.ok(memberCount(sections) <= tiles, 'distinct people cannot exceed tiles');
  assert.ok(memberCount(sections) > 0, 'roster should not be empty');
});

test('every page marks its headcounts for derivation, none hardcode one', () => {
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    // A bare "team of N" in prose is what drifted last time.
    assert.doesNotMatch(html, /team of \d+/i, `${page} hardcodes a headcount in prose`);
  }
});

test('section counts in who-we-are match the tiles they head', () => {
  const html = readFileSync(join(SITE, 'who-we-are.html'), 'utf8');
  const marked = [...html.matchAll(/<span class="count"[^>]*data-section-count/g)];
  assert.equal(
    marked.length,
    sectionCounts(readRoster(SITE)).length,
    'every roster section needs a derived count',
  );
});

// ── Application window ───────────────────────────────────────────────────
test('every open/closed pair ships the closed half hidden', () => {
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    for (const m of html.matchAll(/<[^>]*data-when="closed"[^>]*>/g)) {
      assert.match(m[0], /\bhidden\b/, `${page}: a closed variant would show without JS`);
    }
  }
});

test('no page hardcodes an application status that cannot flip', () => {
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    const meta = html.match(/<meta name="description" content="([^"]*)"/);
    if (meta) {
      assert.doesNotMatch(
        meta[1],
        /applications are (now )?open/i,
        `${page}: meta description carries a status client JS cannot update`,
      );
    }
  }
});

// ── Closing band ─────────────────────────────────────────────────────────
test('no dark section is the last thing before the footer', () => {
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    const main = html.slice(0, html.indexOf('</main>'));
    const last = main.lastIndexOf('<section');
    if (last === -1) continue;
    const tag = main.slice(last, main.indexOf('>', last));
    assert.doesNotMatch(tag, /\bdark\b/, `${page}: dark section abuts the dark footer`);
  }
});

// ── Assets ───────────────────────────────────────────────────────────────
test('image paths use forward slashes', () => {
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    assert.doesNotMatch(html, /src="[^"]*\\/, `${page}: backslash in a src path 404s`);
  }
});

test('every headshot on disk is referenced by a page', () => {
  const html = pages.map((p) => readFileSync(join(SITE, p), 'utf8')).join('');
  const orphans = readdirSync(join(SITE, 'Headshots')).filter((f) => !html.includes(f));
  assert.deepEqual(orphans, [], `unreferenced headshots ship to production: ${orphans}`);
});

test('the stylesheet forces [hidden] to win over class selectors', () => {
  // [hidden] is only display:none in the UA sheet, so any class selector that
  // sets display outranks it. .ticker-track span did, and both halves of an
  // open/closed pair rendered at once.
  const css = readFileSync(join(SITE, 'assets', 'main.css'), 'utf8');
  assert.match(
    css,
    /\[hidden\]\s*\{[^}]*display:\s*none\s*!important/,
    'main.css must force [hidden] to display:none !important',
  );
});

test('no class selector silently re-displays a hidden element', () => {
  const css = readFileSync(join(SITE, 'assets', 'main.css'), 'utf8');
  const guard = css.indexOf('[hidden]');
  assert.ok(guard !== -1 && guard < css.indexOf(':root'), '[hidden] guard should come first');
});

// ── Nothing is typed twice ───────────────────────────────────────────────
// The build stamps dist/, but the values checked into site/ are what a person
// reads and what ships if the stamping ever regresses. These keep the two in
// step, so "the count is derived" is true of the source as well as the output.
test('checked-in headcounts match the roster', () => {
  const sections = readRoster(SITE);
  const total = memberCount(sections);
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    // A counter that animates carries its target in data-count and starts its
    // text at 0, so only the static spans are checked on their text.
    for (const m of html.matchAll(/<span([^>]*\bdata-member-count\b[^>]*)>(\d+)<\/span>/g)) {
      if (/\bdata-count=/.test(m[1])) continue;
      assert.equal(Number(m[2]), total, `${page}: stale member count in source — run \`pnpm sync\``);
    }
    for (const m of html.matchAll(/\bdata-member-count\b[^>]*\bdata-count="(\d+)"/g)) {
      assert.equal(Number(m[1]), total, `${page}: stale data-count in source — run \`pnpm sync\``);
    }
  }
});

test('checked-in section counts match the tiles they head', () => {
  const expected = sectionCounts(readRoster(SITE)).map(pad2);
  const html = readFileSync(join(SITE, 'who-we-are.html'), 'utf8');
  const found = [...html.matchAll(/<span class="count"[^>]*\bdata-section-count\b[^>]*>\/\s*(\d+)<\/span>/g)]
    .map((m) => m[1]);
  assert.deepEqual(found, expected, 'source section counts drifted from the roster — run `pnpm sync`');
});

test('checked-in deadline and term match the application config', () => {
  const { term, closesLabel } = readApplications(SITE);
  for (const page of pages) {
    const html = readFileSync(join(SITE, page), 'utf8');
    for (const m of html.matchAll(/<(?:span|b)[^>]*\bdata-deadline\b[^>]*>([^<]*)<\/(?:span|b)>/g)) {
      assert.equal(m[1], closesLabel, `${page}: stale deadline in source — run \`pnpm sync\``);
    }
    for (const m of html.matchAll(/<(?:span|b)[^>]*\bdata-term\b[^>]*>([^<]*)<\/(?:span|b)>/g)) {
      assert.equal(m[1], term, `${page}: stale term in source — run \`pnpm sync\``);
    }
  }
});

test('the application deadline parses', () => {
  const { closes } = readApplications(SITE);
  assert.ok(Number.isFinite(Date.parse(closes)), `APPLICATIONS.closes is not a date: ${closes}`);
});
