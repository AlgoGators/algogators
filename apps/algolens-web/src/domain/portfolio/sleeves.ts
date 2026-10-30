// Issue #59: a book's sleeves, as the run metadata names them, and the dates they changed.

export interface SleeveSummary {
  name: string;
  positions: number;
  allocation?: number | null;
}

export interface SleeveChange {
  date: string | null;
  added: string[];
  removed: string[];
}

/** "3 positions, 70%": the sleeve's holdings and, when the run lists it, its allocation. */
export function describeSleeve(sleeve: SleeveSummary): string {
  const held = `${sleeve.positions} position${sleeve.positions === 1 ? '' : 's'}`;
  if (sleeve.allocation === undefined || sleeve.allocation === null) return held;
  return `${held}, ${Math.round(sleeve.allocation * 100)}%`;
}

/**
 * One note per change, newest first, at most `limit`: "TREND_FOLLOWING_FAST off from 2025-11-17."
 * A switched-off sleeve's share goes to the remaining sleeves from that date.
 */
export function describeSleeveChanges(
  changes: readonly SleeveChange[] | undefined,
  limit = 3,
): string[] {
  if (!changes || changes.length === 0) return [];
  return [...changes]
    .reverse()
    .slice(0, limit)
    .map(change => {
      const from = change.date ? ` from ${change.date}` : '';
      const parts = [
        ...change.removed.map(name => `${name} off${from}.`),
        ...change.added.map(name => `${name} on${from}.`),
      ];
      return parts.join(' ');
    })
    .filter(note => note.length > 0);
}
