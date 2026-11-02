// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { IdentityApplicationService } from './authService';
import * as authApi from '../../infrastructure/api/authApi';
import { API_BASE_URL } from '../../infrastructure/api/httpClient';

const USER = { id: '1', email: 'member@algogators.org' };

beforeEach(() => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('metadata helpers', () => {
  it('exposes the API base URL and dev-mode flag', () => {
    expect(IdentityApplicationService.getApiUrl()).toBe(API_BASE_URL);
    // VITE_DEV_MODE is unset in the test environment.
    expect(IdentityApplicationService.isDevMode()).toBe(false);
  });
});

describe('restoreSession', () => {
  it('returns the user when a session exists', async () => {
    vi.spyOn(authApi, 'verifySessionRequest').mockResolvedValue({ user: USER, status: 200 });

    await expect(IdentityApplicationService.restoreSession()).resolves.toEqual(USER);
  });

  it('returns null when there is no session (non-dev mode)', async () => {
    vi.spyOn(authApi, 'verifySessionRequest').mockResolvedValue({ user: null, status: 401 });

    await expect(IdentityApplicationService.restoreSession()).resolves.toBeNull();
  });

  it('returns null when the backend is unreachable', async () => {
    vi.spyOn(authApi, 'verifySessionRequest').mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(IdentityApplicationService.restoreSession()).resolves.toBeNull();
  });
});

describe('login', () => {
  it('returns the user on success', async () => {
    vi.spyOn(authApi, 'loginRequest').mockResolvedValue(USER);

    await expect(IdentityApplicationService.login('a@b.c', 'pw')).resolves.toEqual(USER);
    expect(authApi.loginRequest).toHaveBeenCalledWith('a@b.c', 'pw');
  });

  it('rewraps fetch-level network failures with a helpful message', async () => {
    vi.spyOn(authApi, 'loginRequest').mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(IdentityApplicationService.login('a@b.c', 'pw')).rejects.toThrow(
      /Failed to connect to server/
    );
  });

  it('passes through credential errors untouched', async () => {
    vi.spyOn(authApi, 'loginRequest').mockRejectedValue(new Error('Invalid credentials'));

    await expect(IdentityApplicationService.login('a@b.c', 'pw')).rejects.toThrow(
      'Invalid credentials'
    );
  });
});

describe('register', () => {
  it('returns the user on success', async () => {
    vi.spyOn(authApi, 'registerRequest').mockResolvedValue(USER);

    await expect(
      IdentityApplicationService.register('a@b.c', 'pw', 'Al', 'Gator')
    ).resolves.toEqual(USER);
    expect(authApi.registerRequest).toHaveBeenCalledWith('a@b.c', 'pw', 'Al', 'Gator');
  });

  it('rewraps fetch-level network failures with a helpful message', async () => {
    vi.spyOn(authApi, 'registerRequest').mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(
      IdentityApplicationService.register('a@b.c', 'pw', 'Al', 'Gator')
    ).rejects.toThrow(/Failed to connect to server/);
  });

  it('passes through validation errors untouched', async () => {
    vi.spyOn(authApi, 'registerRequest').mockRejectedValue(new Error('Email not authorized'));

    await expect(
      IdentityApplicationService.register('a@b.c', 'pw', 'Al', 'Gator')
    ).rejects.toThrow('Email not authorized');
  });
});

describe('logout', () => {
  it('completes when the backend logout succeeds', async () => {
    vi.spyOn(authApi, 'logoutRequest').mockResolvedValue(undefined);

    await expect(IdentityApplicationService.logout()).resolves.toBeUndefined();
  });

  it('swallows backend failures (local state is cleared regardless)', async () => {
    vi.spyOn(authApi, 'logoutRequest').mockRejectedValue(new Error('unreachable'));

    await expect(IdentityApplicationService.logout()).resolves.toBeUndefined();
  });
});
