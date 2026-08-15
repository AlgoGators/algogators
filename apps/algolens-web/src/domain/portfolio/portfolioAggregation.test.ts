import { describe, expect, it } from 'vitest';
import { aggregateEquityCurves, computePortfolioTotals } from './portfolioAggregation';

describe('computePortfolioTotals', () => {
  it('sums invested and current value and derives the return', () => {
    const totals = computePortfolioTotals([
      { invested: 100, currentValue: 150 },
      { invested: 200, currentValue: 250 },
    ]);

    expect(totals.totalInvested).toBe(300);
    expect(totals.totalValue).toBe(400);
    expect(totals.totalReturn).toBe(100);
    expect(totals.totalReturnPercent).toBeCloseTo((100 / 300) * 100, 6);
  });

  it('reports a negative return when value is below invested', () => {
    const totals = computePortfolioTotals([{ invested: 200, currentValue: 150 }]);

    expect(totals.totalReturn).toBe(-50);
    expect(totals.totalReturnPercent).toBeCloseTo(-25, 6);
  });

  it('guards the percentage when nothing is invested', () => {
    const totals = computePortfolioTotals([{ invested: 0, currentValue: 50 }]);

    expect(totals.totalInvested).toBe(0);
    expect(totals.totalReturn).toBe(50);
    expect(totals.totalReturnPercent).toBe(0); // not Infinity
  });

  it('returns all zeros for an empty portfolio', () => {
    expect(computePortfolioTotals([])).toEqual({
      totalInvested: 0,
      totalValue: 0,
      totalReturn: 0,
      totalReturnPercent: 0,
    });
  });
});

describe('aggregateEquityCurves', () => {
  it('sums equity by date across strategies and sorts ascending', () => {
    const curve = aggregateEquityCurves([
      {
        historicalData: [
          { date: '2026-01-02', value: 150 },
          { date: '2026-01-01', value: 100 },
        ],
      },
      { historicalData: [{ date: '2026-01-01', value: 200 }] },
    ]);

    expect(curve).toEqual([
      { date: '2026-01-01', value: 300 },
      { date: '2026-01-02', value: 150 },
    ]);
  });

  it('keeps dates only one strategy reports', () => {
    const curve = aggregateEquityCurves([
      { historicalData: [{ date: '2026-01-01', value: 10 }] },
      { historicalData: [{ date: '2026-01-03', value: 30 }] },
    ]);

    expect(curve.map(pt => pt.date)).toEqual(['2026-01-01', '2026-01-03']);
  });

  it('returns an empty curve when there are no strategies', () => {
    expect(aggregateEquityCurves([])).toEqual([]);
  });

  it('returns an empty curve when strategies have no history', () => {
    expect(aggregateEquityCurves([{ historicalData: [] }])).toEqual([]);
  });
});
