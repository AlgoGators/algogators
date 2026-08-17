// Build = copy site/ to dist/ verbatim. The site is hand-authored static
// HTML; the "build" exists so the member answers the same four gates as
// every other node member and so publish workflows have a dist/ to ship.
import { cpSync, rmSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = resolve(ROOT, 'dist');

rmSync(DIST, { recursive: true, force: true });
mkdirSync(DIST, { recursive: true });
cpSync(resolve(ROOT, 'site'), DIST, { recursive: true });
console.log('site/ copied to dist/');
