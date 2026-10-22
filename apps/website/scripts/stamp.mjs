// Every number and date the site shows has exactly one source: the roster
// markup for headcounts, the APPLICATIONS config for the intake window. This
// writes those values into a page's markup. `build` applies it to dist/;
// `sync` applies it to site/ so the checked-in fallbacks never go stale.
import { pad2 } from './roster.mjs';

export function stamp(html, { total, perSection, applications }) {
  let out = html
    .replace(/(<span[^>]*\bdata-member-count\b[^>]*>)[^<]*(<\/span>)/g, `$1${total}$2`)
    .replace(/(\bdata-member-count\b[^>]*\bdata-count=")\d+(")/g, `$1${total}$2`);

  // Section counts are positional: the nth marked count heads the nth section.
  let i = 0;
  out = out.replace(
    /(<span class="count"[^>]*\bdata-section-count\b[^>]*>)[^<]*(<\/span>)/g,
    (m, open, close) => `${open}/ ${pad2(perSection[i++] ?? 0)}${close}`,
  );

  return out
    .replace(
      /(<(?:span|b)[^>]*\bdata-deadline\b[^>]*>)[^<]*(<\/(?:span|b)>)/g,
      `$1${applications.closesLabel}$2`,
    )
    .replace(
      /(<(?:span|b)[^>]*\bdata-term\b[^>]*>)[^<]*(<\/(?:span|b)>)/g,
      `$1${applications.term}$2`,
    );
}

/** An animated counter starts its text at 0 and carries its target in data-count. */
export function stampPreservingCounters(html, values) {
  return stamp(html, values).replace(
    /(<span[^>]*\bdata-member-count\b[^>]*\bdata-count="\d+"[^>]*>)[^<]*(<\/span>)/g,
    '$10$2',
  );
}
