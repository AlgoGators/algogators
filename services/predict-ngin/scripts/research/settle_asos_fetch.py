"""settle: IEM ASOS METAR archive (p01i, wxcodes, raw) for stations, routine+specials. Free.
usage: settle_asos_fetch.py START END ST1 ST2 ...  -> data/vendor/settle/asos/<ST>_metar.csv"""
import sys, time
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/vendor/settle/asos"; OUT.mkdir(parents=True, exist_ok=True)
a, b = sys.argv[1], sys.argv[2]
for st in sys.argv[3:]:
    f = OUT / f"{st}_metar.csv"
    if f.exists() and f.stat().st_size > 1000:
        continue
    y1, m1, d1 = a.split("-"); y2, m2, d2 = b.split("-")
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=p01i&data=wxcodes&data=metar"
           "&year1=%s&month1=%s&day1=%s&year2=%s&month2=%s&day2=%s&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M"
           "&trace=T&direct=no&report_type=3&report_type=4" % (st, y1, int(m1), int(d1), y2, int(m2), int(d2)))
    for i in range(5):
        r = requests.get(url, timeout=300)
        if r.status_code == 200:
            break
        time.sleep(10 * (i + 1))
    f.write_text(r.text)
    print(st, r.status_code, r.text.count("\n"), flush=True)
    time.sleep(2)
