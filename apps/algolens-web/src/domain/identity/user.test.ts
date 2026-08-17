import { describe, expect, it } from 'vitest';

import { isInternalRole } from './user';

describe('isInternalRole', () => {
  it('accepts the two internal roles', () => {
    expect(isInternalRole('admin')).toBe(true);
    expect(isInternalRole('general_member')).toBe(true);
  });

  it('rejects external and unknown roles', () => {
    expect(isInternalRole('external')).toBe(false);
    expect(isInternalRole('investor')).toBe(false);
    expect(isInternalRole('ADMIN')).toBe(false);
  });

  it('rejects missing roles', () => {
    expect(isInternalRole(undefined)).toBe(false);
    expect(isInternalRole(null)).toBe(false);
    expect(isInternalRole('')).toBe(false);
  });
});
