import { describe, expect, it } from 'vitest';
import { describeSleeve, describeSleeveChanges } from './sleeves';

describe('describeSleeve', () => {
  it('shows the holdings and the run allocation', () => {
    expect(describeSleeve({ name: 'TF', positions: 3, allocation: 0.7 })).toBe('3 positions, 70%');
    expect(describeSleeve({ name: 'TF', positions: 1, allocation: 1 })).toBe('1 position, 100%');
  });

  it('shows only the holdings when the run does not list the sleeve', () => {
    expect(describeSleeve({ name: 'OLD', positions: 0 })).toBe('0 positions');
    expect(describeSleeve({ name: 'OLD', positions: 2, allocation: null })).toBe('2 positions');
  });
});

describe('describeSleeveChanges', () => {
  it('says nothing without changes', () => {
    expect(describeSleeveChanges(undefined)).toEqual([]);
    expect(describeSleeveChanges([])).toEqual([]);
  });

  it('notes each change newest first, up to the limit', () => {
    const changes = [
      { date: '2025-11-10', added: ['FAST'], removed: [] },
      { date: '2025-11-17', added: [], removed: ['FAST'] },
      { date: '2025-12-01', added: ['NEW'], removed: ['OLD'] },
    ];
    expect(describeSleeveChanges(changes, 2)).toEqual([
      'OLD off from 2025-12-01. NEW on from 2025-12-01.',
      'FAST off from 2025-11-17.',
    ]);
  });

  it('leaves out the date when it is unknown', () => {
    expect(describeSleeveChanges([{ date: null, added: [], removed: ['X'] }])).toEqual(['X off.']);
  });
});
