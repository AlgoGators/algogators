// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PortfolioApplicationService } from './portfolioService';
import { PortfolioApiService } from '../../infrastructure/api/portfolioApi';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('PortfolioApplicationService', () => {
  it('delegates every call to the API service', async () => {
    const strategy = { id: 's' };
    const portfolio = { totalValue: 1 };
    const incubating = [{ id: 'i' }];
    const performance = { positions: [], equity_curve: [] };

    vi.spyOn(PortfolioApiService, 'testConnectivity').mockResolvedValue(undefined);
    vi.spyOn(PortfolioApiService, 'getStrategy').mockResolvedValue(strategy as never);
    vi.spyOn(PortfolioApiService, 'getAllStrategies').mockResolvedValue([strategy] as never);
    vi.spyOn(PortfolioApiService, 'getPortfolioData').mockResolvedValue(portfolio as never);
    vi.spyOn(PortfolioApiService, 'getIncubationStrategies').mockResolvedValue(
      incubating as never
    );
    vi.spyOn(PortfolioApiService, 'getIncubationPerformance').mockResolvedValue(
      performance as never
    );

    await expect(PortfolioApplicationService.testConnectivity()).resolves.toBeUndefined();
    await expect(PortfolioApplicationService.getStrategy('s')).resolves.toBe(strategy);
    await expect(PortfolioApplicationService.getAllStrategies()).resolves.toEqual([strategy]);
    await expect(PortfolioApplicationService.getPortfolioData()).resolves.toBe(portfolio);
    await expect(PortfolioApplicationService.getIncubationStrategies()).resolves.toBe(incubating);
    await expect(PortfolioApplicationService.getIncubationPerformance('s')).resolves.toBe(
      performance
    );

    expect(PortfolioApiService.getStrategy).toHaveBeenCalledWith('s');
    expect(PortfolioApiService.getIncubationPerformance).toHaveBeenCalledWith('s');
  });
});
