import csv, glob, io, os, subprocess, sys
# Compares the CONSERVATIVE book's fills now on the scratch DB with an archived book: fee-compare.py <archive folder>
# Needs PG* and PGDATABASE for the scratch DB (. ~/.scratch-pg; PGDATABASE=$SCRATCH_DB).
base = sys.argv[1]
print([os.path.basename(f) for f in glob.glob(base + '/*')][:10])
f = [x for x in glob.glob(base + '/*') if 'executions' in os.path.basename(x)][0]
cur = subprocess.run(['psql','-X','-c',"copy (select * from trading.executions where portfolio_id='CONSERVATIVE_PORTFOLIO') to stdout with csv header"],capture_output=True,text=True).stdout
def load(rows):
    return {(r['date'][:10], r['symbol'], r['side'], r['execution_type']): r for r in rows}
a = list(csv.DictReader(open(f))); b = list(csv.DictReader(io.StringIO(cur)))
print('fills base', len(a), 'fee', len(b))
A, B = load(a), load(b)
print('same fill keys', A.keys() == B.keys())
trade = cost = 0; syms = {}; qty = 0.0
for k in A.keys() & B.keys():
    x, y = A[k], B[k]
    if float(x['quantity']) != float(y['quantity']) or float(x['price']) != float(y['price']): trade += 1
    d = float(y['total_transaction_costs']) - float(x['total_transaction_costs'])
    if abs(d) > 1e-9:
        cost += 1; syms[k[1][:3]] = round(syms.get(k[1][:3], 0) + d, 4)
        if k[0] <= '2026-10-23': qty += abs(float(y['quantity']))
print('fills with a different quantity or price:', trade)
print('fills with a different cost:', cost, 'cost change by root:', syms, 'contracts in them to the cut:', qty)
from collections import Counter
K = lambda r: (r['date'][:10], r['symbol'], r['side'], r['execution_type'], r['quantity'], r['price'])
ca, cb = Counter(map(K, a)), Counter(map(K, b))
print('only in base:', list((ca - cb).items())[:6])
print('only in fee: ', list((cb - ca).items())[:6])
print('repeat keys base', len(a) - len(A), 'fee', len(b) - len(B))
for r in a:
    if K(r) in (ca - cb): print('base row', {k: r[k] for k in ('exec_id','date','symbol','side','quantity','price','execution_time','created_at','total_transaction_costs')})
