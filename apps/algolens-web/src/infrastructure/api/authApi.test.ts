// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  checkEmailRequest,
  devLoginRequest,
  loginRequest,
  logoutRequest,
  registerRequest,
  verifySessionRequest,
} from './authApi';
import { API_BASE_URL } from './httpClient';

const USER = { id: '1', email: 'member@algogators.org' };

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('verifySessionRequest', () => {
  it('returns the user when the session is valid', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ user: USER })));

    await expect(verifySessionRequest()).resolves.toEqual({ user: USER, status: 200 });
  });

  it('returns a null user on 401 without throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 401)));

    await expect(verifySessionRequest()).resolves.toEqual({ user: null, status: 401 });
  });
});

describe('devLoginRequest', () => {
  it('POSTs to /auth/dev-login and returns the user', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ user: USER }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(devLoginRequest()).resolves.toEqual({ user: USER, status: 200 });
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE_URL}/auth/dev-login`,
      expect.objectContaining({ method: 'POST', credentials: 'include' })
    );
  });

  it('returns a null user when dev login is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 404)));

    await expect(devLoginRequest()).resolves.toEqual({ user: null, status: 404 });
  });
});

describe('loginRequest', () => {
  it('sends credentials and returns the user', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ user: USER }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(loginRequest('a@b.c', 'pw')).resolves.toEqual(USER);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE_URL}/auth/login`);
    expect(JSON.parse(init.body)).toEqual({ email: 'a@b.c', password: 'pw' });
  });

  it('throws the backend error message on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ error: 'Invalid credentials' }, 401))
    );

    await expect(loginRequest('a@b.c', 'bad')).rejects.toThrow('Invalid credentials');
  });

  it('falls back to a generic message when the backend gives none', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 500)));

    await expect(loginRequest('a@b.c', 'pw')).rejects.toThrow('Login failed');
  });
});

describe('registerRequest', () => {
  it('sends snake_case names and returns the user', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ user: USER }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(registerRequest('a@b.c', 'pw', 'Al', 'Gator')).resolves.toEqual(USER);
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      email: 'a@b.c',
      password: 'pw',
      first_name: 'Al',
      last_name: 'Gator',
    });
  });

  it('throws the backend error message on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ error: 'Email not authorized' }, 403))
    );

    await expect(registerRequest('a@b.c', 'pw', 'Al', 'Gator')).rejects.toThrow(
      'Email not authorized'
    );
  });

  it('falls back to a generic message when the backend gives none', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 500)));

    await expect(registerRequest('a@b.c', 'pw', 'Al', 'Gator')).rejects.toThrow(
      'Registration failed'
    );
  });
});

describe('logoutRequest', () => {
  it('POSTs to /auth/logout with credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    await logoutRequest();

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE_URL}/auth/logout`,
      expect.objectContaining({ method: 'POST', credentials: 'include' })
    );
  });
});

describe('checkEmailRequest', () => {
  it('POSTs the email to /auth/check-email with cookie credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ registered: false }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(checkEmailRequest('a@b.c')).resolves.toEqual({
      ok: true,
      registered: false,
      message: undefined,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE_URL}/auth/check-email`);
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body)).toEqual({ email: 'a@b.c' });
  });

  it('reports an already-registered email', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ registered: true })));

    await expect(checkEmailRequest('a@b.c')).resolves.toMatchObject({
      ok: true,
      registered: true,
    });
  });

  it('surfaces the backend message on a rejected email', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ message: 'Email not authorized' }, 403))
    );

    await expect(checkEmailRequest('a@b.c')).resolves.toEqual({
      ok: false,
      registered: false,
      message: 'Email not authorized',
    });
  });
});
