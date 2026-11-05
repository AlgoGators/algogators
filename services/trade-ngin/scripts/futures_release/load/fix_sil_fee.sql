-- SIL's fee: 1.36 per contract = 0.25 commission + 1.10 exchange + 0.01 regulatory.
-- The exchange fee is NYMEX Submission 26-196 (CME SER 9676), effective 2026-04-01. The row held 0.860, from SER-8425.
-- One row, one transaction; stops unless exactly that row is in its old state.
\set ON_ERROR_STOP on
begin;
do $$
declare n int;
begin
  update metadata.contract_metadata
     set "Fee Per Contract" = '1.360',
         "Additional Notes" = replace("Additional Notes",
           'Fee: IBKR 0.25 commission + 0.60 exchange (CME SER-8425; IBKR''s COMEX page lists no SIL row) + 0.01 regulatory.',
           'Fee: IBKR 0.25 commission + 1.10 exchange (NYMEX Submission 26-196, CME SER 9676, effective 2026-04-01; IBKR''s COMEX page lists no SIL row) + 0.01 regulatory.')
   where "Databento Symbol" = 'SIL' and "Fee Per Contract" = '0.860'
     and "Additional Notes" like '%0.60 exchange (CME SER-8425%';
  get diagnostics n = row_count;
  if n <> 1 then raise exception 'expected 1 SIL row in its old state, updated %', n; end if;
end $$;
commit;
