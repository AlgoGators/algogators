# KQ: the four Kalshi-support questions, answered from primary sources

Last revised: 2026-10-16

Research only. No orders, RFQs or quotes were sent. The only authenticated calls were read-only GETs
(`/account/limits`, `/communications/rfqs`, `/communications/quotes`) through `scripts/research/kalshi_flb_api.py`.
No key material was printed. Sources were fetched 2026-10-14 (machine clock). Scratch copies of the PDFs, help pages
and Wayback captures are in the session scratchpad, not in the repo.

| # | Question | Short answer | Confidence |
|---|---|---|---|
| 1 | Can a retail API account quote combo RFQs? | **Yes on paper.** Rule 5.3 says "Any other Member" may quote. The API documents no permission, minimum or agreement, and a Basic-tier retail account can read every open combo RFQ. Not tested end-to-end: no quote was sent. | High that it is permitted; medium that it works in practice |
| 2 | Which CDC print settles KXMEASLES-26? | **Most likely a Thursday Dec 31, 2026 print (data as of Dec 31 noon), settled on Jan 1, 2027.** In all 3 holiday cases since late 2025, CDC moved the release to the previous business day, and Kalshi settled 2024 and 2025 on Jan 1 without waiting for a January release. | Medium (about 65% on the main case) |
| 3 | LIP abuse, revocation, far-from-touch, minimum price | The CRO may revoke status for participation that is "abusive or in any way inconsistent with the purpose of the Program". **No minimum price, price band or "meaningful liquidity" test is published.** Distance is handled by the Discount Factor and by the Target-Size depth walk. | High on what is published; the enforcement standard is unknowable |
| 4 | Maker fees on mention, sports-prop and rain series | **Mention: none (450/450 `quadratic`). Rain: none (63/63). Sports props: only KXNFLANYTD, KXNFL2TD and KXNFLFIRSTTD** have maker fees. MLB props run at 0.5x taker until per-event postseason overrides set them to 1x. Combos (KXMVE*) are `quadratic_with_combo_maker_fees`. | High (as of 2026-10-14; Kalshi can schedule changes) |

---

## 1. Combo RFQs: can a regular retail API account be a quoter?

### Primary sources

**Kalshi Rulebook Rule 5.3(B)**, as amended in the CFTC self-certification of 2026-01-30 (effective 2026-02-14):
<https://www.cftc.gov/filings/orgrules/rules01302638537.pdf>

> "a. A Member (referred to for the purpose of this Rule as the 'Requester') may use Kalshi's pre-execution
> communications system to create a two-sided Request for Quote ('RFQ') message ... This message must contain the
> following information, which is public to all other Members"
>
> "b. **Any other Member** (referred to for the purpose of this Rule as the 'Quoter') may choose to respond to the RFQ
> with a structured response ('Quote'). ... A Quoter may only respond with a Quote that the Quoter has sufficient
> collateral to match at the specified price and quantity and full execution would not result in any position limit
> violations."
>
> "h. Members may only create a Quote and/or a RFQ for the purpose of executing bona fide Transactions."
>
> "i. The Exchange may identify certain markets to be High Volatility Markets ('HVM'). In a HVM, upon Quote
> acceptance, a Quoter will have only 1 second (or another period of time as set forth in the Kalshi Platform) to
> confirm"
>
> "j. A Member may choose to make public their willingness to provide Quotes in a market. If a Member chooses to do
> so, the Exchange will add the Member's unique ID code to a list of 'available Quoters' on that market."

The rule sets only three conditions: collateral, position limits and bona fide intent. It has no membership class,
agreement or approval step.

**API docs.**
- RFQ guide (<https://docs.kalshi.com/getting_started/rfqs.md>):
  - "The RFQ is broadcast to all makers."
  - "Makers respond with quotes containing a `yes_bid` and `no_bid`."
  - "All combo markets are HVMs" (HVM timers: 3 s to confirm, 1 s to execute).
  - "The full REST RFQ lifecycle — including combo-market creation — is also available to subaccount-restricted API
    keys."

  The page states no eligibility requirement.
- Create Quote (<https://docs.kalshi.com/api-reference/communications/create-quote.md>): "Endpoint for creating a
  quote in response to an RFQ". It needs only the standard three signed headers. The body fields are `rfq_id`,
  `yes_bid`, `no_bid`, `rest_remainder`, `post_only` and `subaccount`. The documented errors are 400, 401 and 500.
  **No 403 is documented**, and the page lists no scope or tier requirement.
- Communications WebSocket (<https://docs.kalshi.com/websockets/communications.md>): "RFQ events (RFQCreated,
  RFQDeleted) are sent for all users by default." "Requires authentication."
- Rate limits (<https://docs.kalshi.com/getting_started/rate_limits.md>): RFQ and quote writes share the shard-1
  Write bucket with order writes. The page defines no RFQ-specific tier.
- FIX RFQ (<https://docs.kalshi.com/fix/rfq-messages.md>): "Market Makers - Users who provide quotes (connect via
  RfqMode)". Here "Market Makers" is a role name. The page names no program or agreement.

**Help center, Combos** (<https://help.kalshi.com/markets/combos>, updated 2026-10-10): "When you request a combo,
the platform sends out a quote request to the market and other participants can respond with a price."

**RFQ fee filing**, 2026-07-12 (<https://www.cftc.gov/filings/orgrules/rules0712269458.pdf>): "quoters whose quote is
accepted shall pay the equivalent of a 'maker' fee". This applies to KXMVE trades not involving an order that rested
for more than 5 s. It is a fee term, not an eligibility term.

**Separate programs that *do* need an agreement, and are not required for RFQ quoting:**
- Market Maker Program (<https://help.kalshi.com/markets/market-maker-program>): "Market maker status is granted
  following a thorough review of financial resources, trading experience, and business reputation". This gives
  reduced fees and adjusted position limits.
- Liquidity Provider Program (<https://help.kalshi.com/en/articles/15410219-liquidity-provider-program>): "Members
  who have executed a Market Maker Agreement with Kalshi may become a Designated Liquidity Provider". Selection is by
  auction.

Neither page says RFQ quoting needs these. Rule 5.3 says the opposite.

### Read-only API checks (2026-10-15 ~02:50 UTC)

| Call | Result |
|---|---|
| `GET /account/limits` | `usage_tier: basic`, read 200/s, write 100/s, `grants: []`. This is a plain retail account. |
| `GET /communications/rfqs?status=open&limit=100` | 200 OK. 100 open RFQs, **all combo** (`mve_collection_ticker` set, e.g. `KXMVECROSSCATEGORY-R`), from 96 distinct creator IDs. Fields include `contracts_fp` and `target_cost_dollars`. |
| `GET /communications/quotes` (no filter) | 403 `Either creator_user_id or rfq_creator_user_id must be filled.` Other users' quotes are private, as Rule 5.3(b) says. |
| `GET /communications/quotes?user_filter=self` | 200 OK, `quotes: []`. |

So the retail account has full read access to the combo RFQ feed. Whether the POST is accepted was not tested:
sending a quote is forbidden in this research mode.

### Minimum, approval or agreement?
- **None published** for RFQ quoting.
- The practical floors are collateral for the full RFQ size, because "Quoters do not specify a size", and a 3 s
  confirmation window on combos.
- The tier floor for automation is Basic (100 writes/s), which is ample for quoting.

### Remaining ambiguity
- Kalshi could reject combo quotes from accounts that are not allow-listed without documenting it. The undocumented
  403 on `/communications/quotes` shows that undocumented gates exist. Third-party coverage (Action Network) says
  taking the other side of user combos is for "approved" institutional traders. That claim is secondary and
  conflicts with Rule 5.3(b).
- The cheapest resolution is a single small quote on a combo RFQ, but only when the user explicitly approves one
  live action. A 403 or a permission error would settle the question.
- Under Rule 5.3(h), quotes must be bona fide: quote only at prices we would trade.

**Confidence:**
- Rules and docs permit it: **high** (90%).
- A Basic account's POST /communications/quotes on a KXMVE RFQ is accepted today: **medium** (about 75%).

---

## 2. Measles: which CDC print settles KXMEASLES-26?

### Contract terms and API rules

**Contract terms** (<https://assets.kalshi.com/contract_terms/MEASLES.pdf>, linked from `GET /series/KXMEASLES`;
series `last_updated_ts` 2026-09-29):

> "Underlying: ... the number of measles cases in the United States in a given year according to the website of the
> Centers for Disease Control ('CDC'). Revisions to the Underlying made after Expiration will not be accounted for"
>
> "Last Trading Date: ... the sooner of the date of the first 10:00 AM ET following the occurrence of an event that
> is encompassed in the Payout Criterion or the last day of <year>. The Last Trading Time will be 11:59 PM ET."
>
> "Expiration Date: ... the sooner of the date of the first 10:00 AM ET following the occurrence of an event that is
> encompassed in the Payout Criterion, **the date of the first 10:00 AM ET following the release of the data for all
> of <year>**, or one year after <year>."
>
> "Expiration Value: The Expiration Value is the value of the Underlying as documented by the Source Agency on the
> Expiration Date at the Expiration time."

The product certification (<https://assets.kalshi.com/regulatory/product-certifications/MEASLES.pdf>) carries the
same text. The settlement source is `https://www.cdc.gov/measles/cases-outbreaks.html`.

**API, live 2026 markets** (`GET /events/KXMEASLES-26?with_nested_markets=true`):

| Field | Value |
|---|---|
| `rules_primary` | "If the number of measles in 2026 is above 6000, then the market resolves to Yes." (same pattern for 8000 and 10000) |
| `rules_secondary` | empty |
| `close_time` | 2027-01-01T04:59Z (23:59 ET Dec 31) |
| `expected_expiration_time` | 2026-12-31T15:00Z |
| `expiration_time` / `latest_expiration_time` | 2027-12-31T15:00Z (the "one year after" backstop) |
| `early_close_condition` | "If this event occurs, the market will close the following 10am ET." |
| `settlement_timer_seconds` | 3600 |
| Status | 3000 and 4000 finalized YES; 6000 / 8000 / 10000 active (last 0.76 / 0.27 / 0.10) |

Neither the API nor the terms define "release of the data for all of 2026".

### How Kalshi settled past years

From `GET /historical/markets`:

| Year | Final CDC print used | Kalshi settlement | Note |
|---|---|---|---|
| 2025 (`KXMEASLES-2531`) | **2,065**: page "Updated on December 31, 2025", "As of December 30, 2025" | 2,050 YES, 2,100 NO; settled **2026-01-01 20:37 UTC** | Later CDC revisions (2,144 on Jan 7, then 2,255 and 2,267) ignored. Kalshi did not wait for an "all of 2025" release. |
| 2024 (`MEASLES-24`) | 284 ("As of December 19, 2024", updated Dec 20) | All open strikes (350 and up) NO; settled **2025-01-01 07:28 UTC** | No strike near the value, so this year does not test timing. CDC later posted "As of December 31, 2024 ... 284", "Updated on December 31, 2024", visible by Jan 22, 2025, but Kalshi had already settled. |

In both years Kalshi settled on Jan 1 from what the CDC page showed. It did not wait for a January full-year release.

### CDC cadence and holiday behaviour (Wayback captures of `cdc.gov/measles/data-research/index.html`)

| Period | Normal schedule | Holiday instance | What CDC did |
|---|---|---|---|
| 2024 | Biweekly Friday, data as of Thursday (Oct 11, Nov 8, Nov 22, Dec 6, Dec 20) | Christmas and New Year's | The Dec 20 print stood through Jan 9, 2025. Then "Starting 1/2/25, CDC will update this page monthly." |
| 2025 | Weekly **Wednesday**, "as of noon on Tuesdays" | **Wed Dec 24, 2025**: federal offices closed by executive order (Dec 24 and 26) | **Moved up to Tue Dec 23**: "Updated on December 23, 2025 ... As of December 23, 2025, a total of 2,012". Captured 2025-12-23 23:57 UTC. |
| 2025 | " | Wed Dec 31, 2025 (not a holiday) | Normal: "Updated on December 31, 2025, As of December 30, 2025": 2,065. |
| 2026 | Weekly **Friday**, "as of 12:00PM on Thursday" | **Fri Jun 19, 2026 (Juneteenth)** | **Moved up to Thu Jun 18**: "Updated on June 18, 2026 ... As of June 18, 2026, 2,104". The page changed between Thu 09:36 ET and Fri 05:24 ET. |
| 2026 | " | **Fri Jul 3, 2026 (Independence Day observed)** | **Moved up to Thu Jul 2**: "Updated on July 2, 2026 ... As of July 2, 2026, 2,170". The page changed between Thu 13:27 ET and Fri 10:32 ET. |
| 2026 | " | Fri Aug 28, 2026 (not a holiday) | **Missed, then slipped**: "Updated on August 30, 2026" (a Sunday), "As of August 27": 2,903. Back to normal Fri Sep 4 (3,134). |

The pattern holds in all 3 holiday cases: **when the release day is a federal closure, CDC publishes on the prior
business day, with data as of noon that day.** Non-holiday misses slip later instead (Aug 28 to Aug 30).

### Projection for the 2026 holiday weeks
- **Fri Dec 25.** Expect a Thursday Dec 24 print (as of Dec 24). If an executive order closes Dec 24, as happened in
  2025 (and on Christmas Eve in 2024), expect Wednesday Dec 23.
- **Fri Jan 1.** Expect a **Thursday Dec 31 print (as of Dec 31 noon)**. This is the closest thing to "data for all of
  2026", and it is published before trading closes at 23:59 ET.
- **Kalshi.** As in 2025, Kalshi would settle on Jan 1, 2027 from the value on the page.

### Most likely settling print, with alternatives

| Scenario | Settling print | P |
|---|---|---|
| A | Thu Dec 31, 2026 print (as of Dec 31 noon), settled Jan 1, 2027 | **0.65** |
| B | Wed Dec 30 print (Dec 31 closed or CDC publishes early), settled Jan 1 | 0.07 |
| C | CDC skips the New Year week. The last 2026 print (Dec 23 or 24) is on the page at expiration and Kalshi settles Jan 1 on it | 0.10 |
| D | Kalshi treats the first January 2027 print (about Jan 7-8, or a CDC "as of December 31" year-end post like the one in Jan 2025) as the "release of the data for all of 2026" | 0.08 |
| E | Other: a weekend or late slip (like Aug 30), a Kalshi review under Rule 6.3(c)/7.1, or settlement lag | 0.10 |

### Remaining ambiguity
- "Release of the data for all of <year>" is undefined. Kalshi's 2025 practice treats the last in-year print as
  sufficient, even though that print ran only to Dec 30.
- Kalshi's crossing detection in 2026 has been slow and probably manual:
  - KXMEASLES-26-3000 was crossed by the Sep 4 print (3,134) but closed only on 2026-10-13 15:53 UTC, together with
    4000, which was crossed by the Oct 9 print (4,080).
  - Expect settlement-time lag. A rung crossed by the Dec 24 or Dec 31 print might be settled together with the
    year-end determination.
- Executive-order closures for Dec 24 or Dec 31, 2026 are not known yet. Watch for a Christmas EO in mid-December.

**Confidence: medium.** The CDC move-up pattern holds in 3 of 3 cases. The Kalshi Jan 1 practice holds in 2 of 2,
though only one of those tested the timing.

---

## 3. LIP: abuse, revocation, orders far from the touch, minimum price

### Primary sources
- **Help article** "Liquidity Incentive Program" (<https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program>,
  `lastUpdatedDate` 2026-10-08):
  > "Kalshi monitors for abusive behavior and market manipulation"
  > "Participant status can be revoked for violations"
  > "Program may be terminated at any time"
  > "Orders priced at or better than the Reference Price get full credit (1.0x multiplier)"
  > "Orders priced below the Reference Price get reduced credit: the Discount Factor raised to the number of ticks away"
  > "[The Reference Price] is not always the best bid or ask - a small order alone at the top of the book does not set it"
  > "For each snapshot, Kalshi scores every resting order that helps reach the Target Size on its side"
  > "If a side of the book never reaches the Target Size, no orders on that side qualify for that snapshot"
  > "Minimum payout: $1.00 (rounded down to nearest cent)". Program end: "January 1, 2027".
- **CFTC self-certification** 2026-07-15, *Amendment to August 2025 Liquidity Incentive Program*, effective
  2026-07-30 (<https://www.cftc.gov/filings/orgrules/rules07152610358.pdf>; local copy
  `data/vendor/klip/docs/cftc_lip_2026-07-15.txt`), Appendix A:
  > "Monitoring and Termination of Status: Kalshi shall monitor trading activity and participants' performance and
  > shall retain the right to revoke participant status if Kalshi's Chief Regulatory Officer concludes from review
  > that a participant's participation in the program is abusive or in any way inconsistent with the purpose of the
  > Program."
  > "Program Purpose: The purpose of this Program is to increase liquidity on the central limit order book and
  > thereby enhance pricing efficiency."
  > Cover letter: "Chapter 5 of Kalshi's Rulebook includes prohibitions against fraudulent, non-competitive, unfair
  > or abusive practices, all of which apply to trading under the Program."
  > "If the Qualifying Yes Total Size is greater than or equal to the target size, the procedure is stopped here."
  > "each Qualifying Yes Bid is assigned a score equal to the Discount Factor taken to the Nth power multiplied by its
  > size, where N is the number of ticks between the Reference Yes Price and the price of the Qualifying Yes Bid."
  > Target Size "greater than 100 contracts and less than 20,000 contracts"; Discount Factor "no greater than 1.00";
  > reward "$1 ... $1,000 per calendar day".

### What this means
- **Revocation standard.** The CRO has discretion over participation that is "abusive or in any way inconsistent with
  the purpose". The purpose is CLOB liquidity and pricing efficiency. No examples, safe harbours or appeal process are
  published.
- **Far from the touch.** There is **no distance cutoff and no price band.** Distance is penalised only in two ways:
  - The Discount Factor raised to the power N, with N measured below the Reference Price.
  - The depth walk stops once cumulative size reaches Target Size. Orders deeper than that score zero for the
    snapshot.

  A deep order earns only if the book on that side is thinner than Target Size above it.
- **Minimum price or "meaningful liquidity".** **None published** for LIP.
  - The tracked-changes appendix in the 07-15 filing removed an earlier condition: the Reference Price applied only
    if "the highest yes bid price exists and is less than the highest possible price". The clean text has no such
    exclusion.
  - The only "meaningful" tests are two-sided Target-Size depth (otherwise the snapshot is excluded and the pool is
    scaled down) and the $1 payout floor.
  - For contrast, Kalshi's *Deposit and Trading Reward* filing (2026-09-25, local copy
    `data/vendor/lipaudit/docs/rules09252630037.txt`) does set a band: trades count only from "$0.03 to $0.97 ... to
    discourage non-bona fide or otherwise manipulative trading". It also cites Rule 5.17 abuses (self-matching, wash
    trading, pre-arranged trading). Kalshi uses price bands where it wants them, and LIP has none.

### Remaining ambiguity
- How the CRO would view 1c bids on far-OTM strikes, or 99c stubs, posted mainly to collect pool share. They are bona
  fide resting orders that can fill, but whether they are "inconsistent with the purpose" is a judgement call.
  Nothing published forbids them.
- The implementation could differ from Appendix A in unpublished ways, for example a price band in the scorer.

**Confidence:** high on what is published; unresolvable on how discretion would be applied.

---

## 4. Fees: maker fees on mention, sports-prop and rain series

Source: `GET /series` (15,104 series), `GET /series/fee_changes?show_historical=true` (107 series-level changes; the
latest is dated 2026-09-03, with none scheduled after today) and `GET /events/fee_changes` (27 event-level overrides).
The enums `quadratic`, `quadratic_with_maker_fees`, `quadratic_with_combo_maker_fees` and `flat` are documented at
<https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes.md>. Event overrides are "layered on top of the
parent series' fee structure" (<https://docs.kalshi.com/api-reference/events/get-event-fee-changes.md>).

| Family | Series (count) | fee_type / multiplier | Maker fee? |
|---|---|---|---|
| Mention (ticker contains MENTION, or the title says/mentions) | 450 | all `quadratic` x1 | **No** |
| Rain (KXRAIN and other rain/precip series) | 63 | all `quadratic` x1 | **No** (KXRAIN has one Designated Liquidity Provider under the LP program) |
| Sports props (player/team stat series, about 470) | KXNFLANYTD, KXNFL2TD, KXNFLFIRSTTD | `quadratic_with_maker_fees` x1 | **Yes** |
| | MLB props KXMLBHIT, HR, HRR, KS, OUTS, RBI, SB, TB, TEAMTOTAL, RFI, F3/F5/F7 and others | `quadratic` x0.5 (changed 2026-08-07) | No. Per-event overrides set postseason games (e.g. `KXMLB*-26OCT15...TBCLE`) to `quadratic` x1, still with no maker fee |
| | All other props (KXNFLPASSYDS, KXNBAPTS/REB/AST/3PT, KXNHL*, NCAAF team stats, ...) | `quadratic` x1 | No |
| Combos | KXMVECROSSCATEGORY, -SHARD1, KXMVESPORTSMULTIGAMEEXTENDED | `quadratic_with_combo_maker_fees` x1 (since 2026-08-20) | Quoter pays maker-equivalent per the 2026-07-12 filing |

Game-line series (KXNFLGAME, KXNFLSPREAD, KXNFLTOTAL, NBA/NHL/NCAA games, soccer games and so on) and KXMLBGAME
(x0.5, with postseason overrides to x1) do carry maker fees. They are not props.

**Confidence: high** for the state at 2026-10-14 ~23:00 ET. Fee types can change on a scheduled timestamp, so re-pull
`/series/fee_changes` and `/events/fee_changes` before trading.

---

## What is still worth asking Kalshi (narrowed)
1. Confirm that a Basic-tier member's `POST /communications/quotes` on KXMVE RFQs is permitted. This needs only a
   yes/no answer, or one approved test quote.
2. Confirm that KXMEASLES-26 settles from the CDC page on the morning of Jan 1, 2027, i.e. whether the Dec 31 print
   counts as the release of the data for all of 2026.
3. Ask whether 1c/99c Target-Size LIP orders on thin sides are considered consistent with the Program purpose.
