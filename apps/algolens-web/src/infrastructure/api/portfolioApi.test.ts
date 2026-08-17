// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE_URL } from './httpClient';
import { PortfolioApiService } from './portfolioApi';

function jsonResponse(body: unknown, init?: { status?: number; contentType?: string }) {
  const status = init?.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status-${status}`,
    headers: new Headers({ 'content-type': init?.contentType ?? 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this;
    },
  } as unknown as Response;
}

function makeStrategy(id: string, invested: number, currentValue: number, historical: unknown[]) {
  return { id, name: id, invested, currentValue, positions: [], historicalData: historical };
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

describe('getStrategy', () => {
  it('fetches one strategy by id', async () => {
    const strategy = makeStrategy('trend', 100, 120, []);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(strategy));
    vi.stubGlobal('fetch', fetchMock);

    const got = await PortfolioApiService.getStrategy('trend');

    expect(got.id).toBe('trend');
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/portfolio/strategy/trend`);
  });
});

describe('getAllStrategies', () => {
  it('unwraps the strategies array', async () => {
    const body = { strategies: [makeStrategy('a', 1, 2, [])] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)));

    const got = await PortfolioApiService.getAllStrategies();

    expect(got).toHaveLength(1);
    expect(got[0].id).toBe('a');
  });

  it('propagates auth failures from fetchWithAuth', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, { status: 500 })));

    await expect(PortfolioApiService.getAllStrategies()).rejects.toThrow(/API request failed/);
  });
});

describe('getIncubationStrategies', () => {
  it('returns the incubating strategies', async () => {
    const body = { incubating_strategies: [{ id: 'incub' }] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)));

    await expect(PortfolioApiService.getIncubationStrategies()).resolves.toEqual([
      { id: 'incub' },
    ]);
  });

  it('defaults to an empty list when the key is absent', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({})));

    await expect(PortfolioApiService.getIncubationStrategies()).resolves.toEqual([]);
  });
});

describe('getIncubationPerformance', () => {
  it('URL-encodes the id and fills missing keys', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);

    const got = await PortfolioApiService.getIncubationPerformance('a b/c');

    expect(got).toEqual({ positions: [], equity_curve: [] });
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/portfolio/incubation/a%20b%2Fc/performance`
    );
  });

  it('passes through provided positions and equity curve', async () => {
    const body = { positions: [{ symbol: 'ES' }], equity_curve: [{ date: 'd', equity: 1 }] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)));

    await expect(PortfolioApiService.getIncubationPerformance('x')).resolves.toEqual(body);
  });
});

describe('getPortfolioData', () => {
  it('aggregates totals and merges historical data across strategies', async () => {
    const stratA = makeStrategy('a', 100, 150, [
      { date: '2026-01-02', value: 150 },
      { date: '2026-01-01', value: 100 },
    ]);
    const stratB = makeStrategy('b', 200, 250, [{ date: '2026-01-01', value: 200 }]);

    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith('/portfolio/strategies')) {
        return jsonResponse({ strategies: [{ id: 'a' }, { id: 'b' }] });
      }
      if (url.endsWith('/portfolio/strategy/a')) return jsonResponse(stratA);
      if (url.endsWith('/portfolio/strategy/b')) return jsonResponse(stratB);
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const data = await PortfolioApiService.getPortfolioData();

    expect(data.totalInvested).toBe(300);
    expect(data.totalValue).toBe(400);
    expect(data.totalReturn).toBe(100);
    expect(data.totalReturnPercent).toBeCloseTo((100 / 300) * 100);
    // merged by date, sorted ascending
    expect(data.historicalData).toEqual([
      { date: '2026-01-01', value: 300 },
      { date: '2026-01-02', value: 150 },
    ]);
  });

  it('handles an empty strategy list without dividing by zero', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ strategies: [] }))
    );

    const data = await PortfolioApiService.getPortfolioData();

    expect(data.totalInvested).toBe(0);
    expect(data.totalReturnPercent).toBe(0);
    expect(data.strategies).toEqual([]);
    expect(data.historicalData).toEqual([]);
  });
});

describe('testConnectivity', () => {
  it('runs all three probes on the happy path', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);

    await PortfolioApiService.testConnectivity();

    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('logs but does not throw when the probes fail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(PortfolioApiService.testConnectivity()).resolves.toBeUndefined();
  });

  it('reads the error body when the authenticated probe is rejected', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.credentials === 'include') return jsonResponse({ error: 'no' }, { status: 401 });
      return jsonResponse({ status: 'ok' });
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(PortfolioApiService.testConnectivity()).resolves.toBeUndefined();
  });
});
