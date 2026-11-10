\pset format unaligned
\pset footer off
\echo == login
select current_user, current_database(), to_char(now(),'YYYY-MM-DD HH24:MI TZ') as at;
select string_agg(rolname, ', ' order by rolname) as member_of from pg_roles where pg_has_role(current_user, oid, 'member');
select 'create temp tables: '||has_database_privilege(current_database(),'TEMP');
\echo == what the sitting needs, and whether this login has it (only MISSING lines matter)
with need(tbl, priv) as (values
 ('futures_data.ohlcv_1d','SELECT,INSERT,DELETE'), ('futures_data.ohlcv_1d_raw','SELECT,INSERT,DELETE'),
 ('futures_data.contract_ohlcv_1d','SELECT,INSERT,DELETE'), ('metadata.contract_metadata','SELECT,INSERT,UPDATE'),
 ('trading.equity_curve','SELECT,INSERT,DELETE'), ('trading.executions','SELECT,INSERT,DELETE'),
 ('trading.live_results','SELECT,INSERT,DELETE'), ('trading.live_run_metadata','SELECT,INSERT,UPDATE,DELETE'),
 ('trading.positions','SELECT,INSERT,DELETE'), ('trading.signals','SELECT,INSERT,DELETE'),
 ('trading.corp_action_applied','SELECT,INSERT,DELETE'), ('trading.strategy_trading_days_metadata','SELECT,INSERT,UPDATE'),
 ('backtest.results','SELECT,INSERT'), ('backtest.equity_curve','SELECT,INSERT'), ('backtest.executions','SELECT,INSERT'),
 ('backtest.final_positions','SELECT,INSERT'), ('backtest.run_metadata','SELECT,INSERT')),
x as (select tbl, unnest(string_to_array(priv,',')) p from need)
select case when to_regclass(tbl) is null then 'NO SUCH TABLE ' || tbl
            when has_table_privilege(tbl,p) then 'ok      ' || tbl || ' ' || p
            else 'MISSING ' || tbl || ' ' || p end
from x order by 1;
