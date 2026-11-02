import type { HistoricalDataPoint, Strategy } from './portfolioData';

/**
 * Portfolio-level aggregation math shared by the API layer (getPortfolioData)
 * and the strategy-builder view model (computeCombinedMetrics).
 *
 * Pure functions: deterministic, no side effects.
 */

export interface PortfolioTotals {
  totalInvested: number;
  totalValue: number;
  totalReturn: number;
  totalReturnPercent: number;
}

/**
 * Sum invested capital and current value across strategies and derive the
 * absolute and percentage return. The percentage is guarded: a portfolio with
 * nothing invested reports 0%, never NaN or Infinity.
 */
export function computePortfolioTotals(
  strategies: ReadonlyArray<Pick<Strategy, 'invested' | 'currentValue'>>
): PortfolioTotals {
  const totalInvested = strategies.reduce((sum, s) => sum + s.invested, 0);
  const totalValue = strategies.reduce((sum, s) => sum + s.currentValue, 0);
  const totalReturn = totalValue - totalInvested;
  const totalReturnPercent = totalInvested > 0 ? (totalReturn / totalInvested) * 100 : 0;

  return { totalInvested, totalValue, totalReturn, totalReturnPercent };
}

/**
 * Combine per-strategy equity curves into a single curve: sum each strategy's
 * REAL historical equity by date, then sort ascending by date. Dates missing
 * from a strategy simply contribute nothing on that day.
 */
export function aggregateEquityCurves(
  strategies: ReadonlyArray<Pick<Strategy, 'historicalData'>>
): HistoricalDataPoint[] {
  const equityByDate = new Map<string, number>();
  strategies.forEach(s => {
    s.historicalData.forEach(pt => {
      equityByDate.set(pt.date, (equityByDate.get(pt.date) || 0) + pt.value);
    });
  });

  return Array.from(equityByDate.entries())
    .map(([date, value]) => ({ date, value }))
    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
}
