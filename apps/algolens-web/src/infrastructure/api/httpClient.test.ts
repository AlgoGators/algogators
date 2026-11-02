// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE_URL, fetchWithAuth, log } from './httpClient';

function jsonResponse(body: unknown, init?: { status?: number; contentType?: string }) {
  const status = init?.status ?? 200;
  const resp = {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status-${status}`,
    headers: new Headers({ 'content-type': init?.contentType ?? 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this;
    },
  };
  return resp as unknown as Response;
}

beforeEach(() => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('log', () => {
  it('logs with and without a data payload', () => {
    log('info', 'plain message');
    log('error', 'with payload', { a: 1 });
    expect(console.info).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledTimes(1);
  });
});

describe('fetchWithAuth', () => {
  it('returns the response on success and sends credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const resp = await fetchWithAuth(`${API_BASE_URL}/portfolio/strategies`);

    expect(resp.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE_URL}/portfolio/strategies`,
      expect.objectContaining({ credentials: 'include' })
    );
  });

  it('throws the proxy-misconfiguration error when HTML comes back', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse('<html>', { contentType: 'text/html' }))
    );

    await expect(fetchWithAuth('/x')).rejects.toThrow(/HTML instead of JSON/);
  });

  it('treats 401 as an expired session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, { status: 401 })));

    await expect(fetchWithAuth('/x')).rejects.toThrow(/Session expired/);
  });

  it('treats 422 as an expired session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, { status: 422 })));

    await expect(fetchWithAuth('/x')).rejects.toThrow(/Session expired/);
  });

  it('surfaces other HTTP errors with the response body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ error: 'boom' }, { status: 500 }))
    );

    await expect(fetchWithAuth('/x')).rejects.toThrow(/API request failed: 500/);
  });

  it('still throws the HTTP error when the error body cannot be read', async () => {
    const resp = jsonResponse({}, { status: 503 });
    (resp as unknown as { text: () => Promise<string> }).text = async () => {
      throw new Error('unreadable');
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(resp));

    await expect(fetchWithAuth('/x')).rejects.toThrow(/API request failed: 503/);
  });

  it('diagnoses network failures as CORS/back-end-down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(fetchWithAuth('/x')).rejects.toThrow(/Network error: Cannot reach/);
  });

  it('diagnoses explicit CORS failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('blocked by cors policy')));

    await expect(fetchWithAuth('/x')).rejects.toThrow(/CORS error/);
  });

  it('rethrows TypeErrors it cannot classify', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('something weird')));

    await expect(fetchWithAuth('/x')).rejects.toThrow('something weird');
  });

  it('rethrows non-TypeError failures untouched', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('plain failure')));

    await expect(fetchWithAuth('/x')).rejects.toThrow('plain failure');
  });
});
