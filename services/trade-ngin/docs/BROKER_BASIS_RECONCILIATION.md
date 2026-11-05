# Broker basis reconciliation: the book's adjusted basis against a broker statement

Scope: **equities only**. Futures carry no splits and no dividends, and nothing in this document
touches a futures write path.

This document exists because `trading.positions.average_price` for an equity is **not** the
number a broker statement prints. The two disagree by construction, by an amount that is
knowable exactly, and the whole point of writing the rule down is that a future reconciliation
knows which differences are *expected* and which are defects. Without it, the first person to
put a statement next to the book will either "fix" a correct number or accept a wrong one.

Read `AVERAGE_PRICE_LIFECYCLE.md` first: it says what `average_price` means at each step of a
live day. This document says how the meaning it settles on (COST BASIS, in the **adjusted**
frame) relates to the outside world.

---

## 1. The two frames

### Book (adjusted) frame: what this engine stores

`trading.positions.average_price` is restated on **every class-1 ex-date the book held
through**, by `CorporateActionsApplier::apply` (`src/live/corporate_actions_applier.cpp`):

| event | quantity | basis |
|---|---|---|
| split, factor `F` | `q ×= F` | `B /= F` |
| ADR ratio split, factor `F` | `q ×= F` | `B /= F` |
| dividend `d`, raw ex-date close `c` | unchanged | `B /= (1 + d/c)` |

`c` is the **RAW** close **on the ex-date**, not an adjusted one and not the prior close
(`corporate_actions_applier.hpp`, the comment on `close_at_ex_date`). It has
to be the raw ex-date close because the applier's per-event rescale must equal
`compute_backward_adjustment_factors`' per-event step, and that works in raw closes; an
adjusted close carries every LATER event in the window, so under stacked events the two frames
diverge.

A spin-off restates the parent's basis and leaves its share count alone
(`src/live/corporate_actions_lifecycle.cpp`); the child is handled as in identity 1 below.

Marks are the back-adjusted close **anchored at the newest loaded bar**, so today's mark
is raw and the whole series behind it is expressed in today's units.

**Consequence:** a dividend is inside `total_pnl` *via the basis*, not as cash.
`trading.live_results.total_dividend_income` is **informational only** and must never be added
to a P&L total (`CORP_ACTIONS_DATA_BOUNDARY.md`, and two tests pin it from both directions).

### Broker frame: what a statement prints

- Quantity is restated by **splits only**, identical to the book.
- Cost basis is **never** touched by a dividend.
- Dividend cash is credited on the **pay** date, not the ex-date.
- A spinoff **delivers child shares** plus cash-in-lieu for the fractional remainder.

---

## 2. The identities, per open position, at any date T

### Identity 1: quantity

```
q_book == q_broker            exactly
```

Exceptions, and only these:

| exception | shape | tolerance |
|---|---|---|
| **Spinoffs the terms feed can see** | parent quantity is correct and the child is received; under the default `spinoff_child_policy = liquidate_at_first_close` the child is sold at its first close, so the book holds none of it while a broker statement shows the receipt and the sale; reconcile against the two `CORPACTION_<child>_<ex_date>` executions, not against the position | quantity exact on the parent; the child appears only in `trading.executions` |
| **Spinoffs the terms feed cannot see** (after 2025-08-29, or a child with no price series) | the event is refused whole and logged as `SPINOFF NOT APPLIED`: parent quantity is correct, but its basis is PRE-spinoff against a POST-spinoff price series and the child is absent | none: this is a data gap, not a tolerance; the run says so every time |
| **Split fractions** | the book keeps a fractional share; the broker pays cash in lieu | `\|Δq\| < 1` share, AND a CIL execution must exist for `Δq × mark` |

A quantity difference outside these is a defect. Do not tolerate it.

### Identity 2: basis

```
B_broker == B_book × Π_i (1 + d_i / c_i)
```

over the dividends applied since the position's inception. Splits **cancel** (both frames
divide by `F`), so only dividends appear in the product. Exact in real arithmetic; **tolerance
1e-6 relative** for the float chain. `raw_basis` multiplies the DIVIDEND rows only and skips
every other row, spin-offs and terminations included (`src/live/broker_frame.cpp`).

Each factor `(1 + d_i / c_i)` is exactly `PositionAdjustment.ratio_change`, which is what the
`basis_ratio` column on `trading.corp_action_applied` stores (§4). A row written before that
column existed carries NULL, and its chain can only be recomputed by joining back to `ohlcv_1d`
for the raw ex-date closes.

### Identity 3: P&L is NOT reconciled dollar-for-dollar

```
U_book            = q (M − B_book)
U_broker + D      = q (M − B_broker) + Σ q_i·d_i
```

These are **not** equal, and expecting them to be is the mistake this section exists to
prevent. For a single dividend the gap is exactly

```
U_book − (U_broker + D) = q · d · (B_broker − (c + d)) / (c + d)
```

the dividend times the *relative distance* between the raw basis and the cum-dividend close.
It is **zero only when the position was bought at the cum-dividend close**. In general, over
`n` applied dividends:

```
gap = q · B_broker · (1 − 1 / Π_i r_i) − q · Σ_i d_i / S_i,      r_i = 1 + d_i / c_i
```

where `S_i` is the product of the split factors applied *after* dividend `i`: a dividend paid
before a 4:1 split was paid on a quarter of today's shares, so it is charged at the share count
held then, not today's (`expected_pnl_gap`, `include/trade_ngin/live/broker_frame.hpp`). Only
SPLIT and ADR_SPLIT rows count as share-count changes; a spin-off or termination row changes
neither term.

**The rule:**

1. Reconcile **quantity** (identity 1) exactly.
2. Reconcile **basis** (identity 2) exactly, to 1e-6 relative.
3. Reconcile **dividend cash per event**: `corp_action_applied.total_cash` against the broker's
   dividend ledger, tolerance `$0.01 × q`, allowing for ex→pay date timing (the broker credits
   on the pay date; the book books nothing on either date).
4. Report the **expected P&L gap** by the formula above as an **informational line**, tolerance
   formula ± $0.01. Never treat a matching gap as a break, and never treat a *mismatching* gap
   as rounding.

---

## 3. Worked example

100 shares of a $100 stock, bought at the cum-dividend close of 100.63. It pays `d = 0.63`
with a raw ex-date close of `c = 100.00`. Mark today `M = 105`.

```
r        = 1 + 0.63/100.00       = 1.0063
B_broker = 100.63                            (broker never moves it)
B_book   = 100.63 / 1.0063       = 100.00    (what trading.positions holds)

U_book        = 100 × (105 − 100.00)         = 500.00
U_broker + D  = 100 × (105 − 100.63) + 63.00 = 500.00
gap           = 100 × 0.63 × (100.63 − 100.63)/100.63 = 0.00
```

Zero, because the buy was at the cum close. Move the buy to 90.00 instead:

```
B_book = 90.00 / 1.0063 = 89.4366
U_book       = 100 × (105 − 89.4366)         = 1556.34
U_broker + D = 100 × (105 − 90.00) + 63.00   = 1563.00
gap          = 100 × 0.63 × (90.00 − 100.63)/100.63 = −6.66      ✓ 1556.34 − 1563.00
```

$6.66 on a $9,000 position is small; it is also **permanent and cumulative**, and it is the
number an operator will otherwise spend an afternoon on.

---

## 4. Where this is asserted

| site | what it checks |
|---|---|
| `src/live/broker_frame.{hpp,cpp}` | pure `raw_basis(adj_basis, applied_dividends)` and `expected_pnl_gap(...)`, the two functions above, with no DB and no I/O |
| `tests/live/corp_actions/test_broker_frame.cpp` | `RawBasisRecoveredFromAdjustedBasisAndDividendChain`, `PnLGapEqualsDividendTimesBasisDistance` |
| `tests/live/test_broker_frame_event_classes.cpp` | only SPLIT and ADR_SPLIT discount an earlier dividend; a spin-off does not; a termination's NULL ratio does not silence the chain; an unknown dividend ratio does |
| `tests/live/corp_actions/test_broker_frame_db.cpp` | `AppliedRowCarriesTheRatioNeededToInvertIt`: the `basis_ratio` column exists, is written, and inverts the stored basis |
| `trading.corp_action_applied.basis_ratio` (migration 006) | the per-event factor, so identity 2 is computable from the ledger alone |
| the live equity runner, after the class-1 apply | one INFO line per adjusted symbol: `F-8 basis frames \| SYM adjusted=X raw-equivalent=Y (n dividend event(s) …)` (`F-8 basis frames` is a fixed prefix in the runner's log text: search the log for it), and a WARN naming the symbol when a pre-006 row makes the chain uninvertible |

All of it landed with AlgoGators/trade-ngin#82. The runner logs the raw-equivalent basis only;
`expected_pnl_gap` has no production caller and is there for a reconciliation and its tests.

**Until a broker adapter exists this is a statement compare done by hand.** `src/broker` does
not exist. No automated compare exists; the rule, the pure functions and the column stand without it.

### The query

Per symbol, over one portfolio/strategy/name, this recovers the broker-frame basis from the
ledger with no join to `ohlcv_1d`:

```sql
SELECT p.symbol,
       p.average_price                                   AS basis_book,
       p.average_price * exp(sum(ln(a.basis_ratio)))     AS basis_broker,
       count(*)                                          AS dividends_applied
  FROM trading.positions p
  JOIN trading.corp_action_applied a
    ON a.portfolio_id = p.portfolio_id
   AND a.strategy_id  = p.strategy_id
   AND a.symbol       = p.symbol
   AND a.action_type  = 'DIVIDEND'
   AND a.basis_ratio IS NOT NULL
 WHERE p.date = $1 AND p.portfolio_id = $2 AND p.strategy_id = $3
 GROUP BY p.symbol, p.average_price;
```

`exp(sum(ln(...)))` rather than a product aggregate because PostgreSQL has no `product()`;
every `basis_ratio` is `> 0` by construction (`1 + d/c` with `c > 0`, or `F > 0`), so the log
is always defined. **Rows with `basis_ratio IS NULL` are rows written before migration 006**:
they are not zero-effect, they are *unknown*, and a chain containing one cannot be inverted
from the ledger. Say so in the reconciliation rather than treating NULL as 1.0.

### Checking it on a book

Replay a window in which a held position goes through a dividend. For a position opened by one
fill and held through dividends only, `avg_price × Π basis_ratio` from the de-duplication rows
must equal the fill price in `trading.executions` to 1e-6 relative, and the runner's log line
must show both numbers.

A worked example on real data: META's dividend of 2026-06-15. The `basis_ratio` the equity book
stored for it in `trading.corp_action_applied`, 1.0008846127923434, equals 1 + 0.525 / 593.48 (the
dividend over the raw ex-date close) to every digit. The arithmetic can be checked from these
numbers; the stored value itself is in the database, not in this repository, so checking it needs a
read of that table. The split path of `basis_ratio` is pinned by the unit tests in the table above; on real
data it is unmeasured.

---

## 5. What this rule does NOT say

- It does not say the book is wrong. The adjusted frame is the correct frame for a
  total-return series: it is what keeps returns continuous across an ex-date, and it is what
  makes the backtest and the live book comparable.
- It does not license restating the broker's numbers into the book's frame in reporting. Report
  what each frame says and the expected gap between them.
- It says nothing about **realized** P&L on a closed position. A closed row carries no basis
  (`AVERAGE_PRICE_LIFECYCLE.md` rule 7, `average_price = 0`), so identity 2 has no subject
  there. The realized figure on the exit was struck against the *adjusted* basis, and it
  therefore carries the same gap, permanently. That is the reason for storing
  `basis_ratio`: after the row closes, the ledger is the only remaining record of the chain.
