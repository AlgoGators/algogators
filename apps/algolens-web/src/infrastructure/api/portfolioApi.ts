import type { Strategy, PortfolioData } from '../../domain/portfolio/portfolioData';
import type { IncubatingStrategy, IncubationPerformance } from '../../domain/portfolio/incubationData';
import {
  aggregateEquityCurves,
  computePortfolioTotals,
} from '../../domain/portfolio/portfolioAggregation';
import { API_BASE_URL, fetchWithAuth, log } from './httpClient';

export class PortfolioApiService {
  // Debug method to test backend connectivity (call from browser console)
  static async testConnectivity(): Promise<void> {
    log('info', '=== CONNECTIVITY TEST START ===');
    log('info', `Testing connection to: ${API_BASE_URL}`);

    // Test 1: Check if we can reach the backend at all (health endpoint)
    try {
      log('info', 'Test 1: Attempting health check...');
      const healthUrl = `${API_BASE_URL}/health`;
      const response = await fetch(healthUrl, { method: 'GET' });
      log('info', `Health check response: ${response.status} ${response.statusText}`);
      const data = await response.json();
      log('info', 'Health check data:', data);
    } catch (e) {
      log('error', 'Health check FAILED:', e);
    }

    // Test 2: Check auth endpoint
    try {
      log('info', 'Test 2: Testing auth endpoint (should get 401 without token)...');
      const authTestUrl = `${API_BASE_URL}/portfolio/strategies`;
      const response = await fetch(authTestUrl, { method: 'GET' });
      log('info', `Auth test response: ${response.status} - Expected 401 if no token, 200 if CORS misconfigured`);
    } catch (e) {
      log('error', 'Auth endpoint test FAILED (likely CORS issue):', e);
    }

    // Test 3: Check the authenticated request using the session cookie
    try {
      log('info', 'Test 3: Testing authenticated request with session cookie...');
      const authTestUrl = `${API_BASE_URL}/portfolio/strategies`;
      const response = await fetch(authTestUrl, {
        method: 'GET',
        credentials: 'include',
      });
      log('info', `Authenticated request response: ${response.status} ${response.statusText} (200 if logged in, 401 if not)`);
      if (response.ok) {
        const data = await response.json();
        log('info', 'Response data:', data);
      } else {
        const text = await response.text();
        log('error', 'Error response body:', text);
      }
    } catch (e) {
      log('error', 'Authenticated request FAILED:', e);
    }

    log('info', '=== CONNECTIVITY TEST COMPLETE ===');
  }

  static async getStrategy(strategyId: string): Promise<Strategy> {
    log('info', `getStrategy(${strategyId}) called`);
    const url = `${API_BASE_URL}/portfolio/strategy/${strategyId}`;
    log('info', `Fetching strategy from: ${url}`);

    const response = await fetchWithAuth(url);
    const data = await response.json();

    log('info', `Strategy ${strategyId} response:`, {
      id: data.id,
      name: data.name,
      invested: data.invested,
      currentValue: data.currentValue,
      positionsCount: data.positions?.length,
      historicalDataCount: data.historicalData?.length,
    });

    return data;
  }

  static async getAllStrategies(): Promise<Strategy[]> {
    const response = await fetchWithAuth(`${API_BASE_URL}/portfolio/strategies`);
    const data = await response.json();
    return data.strategies;
  }

  static async getIncubationStrategies(): Promise<IncubatingStrategy[]> {
    const response = await fetchWithAuth(`${API_BASE_URL}/portfolio/incubation`);
    const data = await response.json();
    return data.incubating_strategies || [];
  }

  static async getIncubationPerformance(strategyId: string): Promise<IncubationPerformance> {
    const encodedId = encodeURIComponent(strategyId);
    const response = await fetchWithAuth(`${API_BASE_URL}/portfolio/incubation/${encodedId}/performance`);
    const data = await response.json();
    return {
      positions: data.positions || [],
      equity_curve: data.equity_curve || [],
    };
  }

  static async getPortfolioData(): Promise<PortfolioData> {
    log('info', '=== getPortfolioData() START ===');

    // Fetch all strategies
    log('info', `Fetching strategies from: ${API_BASE_URL}/portfolio/strategies`);
    const strategiesResponse = await fetchWithAuth(`${API_BASE_URL}/portfolio/strategies`);

    log('info', 'Parsing strategies response JSON...');
    const strategiesData = await strategiesResponse.json();
    log('info', 'Strategies data received:', strategiesData);

    const strategySummaries = strategiesData.strategies;
    log('info', `Found ${strategySummaries?.length || 0} strategy summaries`);

    if (!strategySummaries || strategySummaries.length === 0) {
      log('warn', 'No strategies found in response');
    }

    // Fetch detailed data for each strategy
    log('info', 'Fetching detailed data for each strategy...');
    const strategies: Strategy[] = await Promise.all(
      strategySummaries.map(async (summary: any, index: number) => {
        log('info', `Fetching strategy ${index + 1}/${strategySummaries.length}: ${summary.id}`);
        const strategy = await this.getStrategy(summary.id);
        log('info', `Strategy ${summary.id} fetched successfully`);
        return strategy;
      })
    );
    log('info', `All ${strategies.length} strategies fetched`);

    // Calculate portfolio totals
    log('info', 'Calculating portfolio totals...');
    const { totalInvested, totalValue, totalReturn, totalReturnPercent } =
      computePortfolioTotals(strategies);

    // Aggregate historical data
    log('info', 'Aggregating historical data...');
    const historicalData = aggregateEquityCurves(strategies);

    const result = {
      totalValue,
      totalInvested,
      totalReturn,
      totalReturnPercent,
      strategies,
      historicalData,
    };

    log('info', '=== getPortfolioData() SUCCESS ===', {
      totalValue,
      totalInvested,
      totalReturn,
      totalReturnPercent,
      strategiesCount: strategies.length,
      historicalDataPoints: historicalData.length,
    });

    return result;
  }
}
