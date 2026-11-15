"""Build watch.html (Ember Watch) from the real Suomi-NPP detections in data/ember.sqlite.

Run from the project root, venv active:
    python build_watch.py
Needs watch_template.html in the same folder. Writes watch.html next to it.
Read-only: it never changes the database.
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
DB = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "data" / "ember.sqlite")
OPERATIONAL = [2024, 2025]          # years being checked; everything else is setup/baseline
ZONES = ["Marmara & NW", "Aegean", "Mediterranean", "Black Sea", "Central Anatolia", "Southeast", "East"]
CONF_OK = {"nominal", "high", "n", "h"}
TYPE_OK = {"0", "0.0", "vegetation", "presumed vegetation fire"}


def zone(lat, lon):
    """Approximate coordinate boxes (not official regions; may include neighbouring territory)."""
    if lat >= 39.8 and lon < 31: return 0
    if lat >= 40.3 and lon >= 31: return 3
    if lon < 30 and lat < 39.8: return 1
    if lat < 37.6 and 30 <= lon < 37.5: return 2
    if lon >= 37.5 and lat < 38.5: return 5
    if lon >= 37.5: return 6
    return 4


con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = con.execute(
    "select acq_date, latitude, longitude, confidence_class, fire_type "
    "from detections where data_origin='real' and platform like '%NPP%'"
).fetchall()
print(f"Real Suomi-NPP rows in database: {len(rows):,}")
print("confidence_class values:", dict(Counter(str(r[3]).lower() for r in rows)))
print("fire_type values:", dict(Counter(str(r[4]).lower() for r in rows)))

keep = [r for r in rows if str(r[3]).lower() in CONF_OK and str(r[4]).lower() in TYPE_OK]
if not keep:
    sys.exit("No rows passed the filters. Send me the two 'values' lines printed above and I will adjust CONF_OK / TYPE_OK.")

counts = defaultdict(int)
totals = Counter()
for d, lat, lon, _, _ in keep:
    dt = date.fromisoformat(str(d)[:10])
    w = min((dt.timetuple().tm_yday - 1) // 7, 51)
    counts[(zone(lat, lon), dt.year, w)] += 1
    totals[dt.year] += 1

years = sorted(totals)
data = {
    "z": ZONES, "y": years, "op": [y for y in OPERATIONAL if y in years],
    "d": [[[counts[(z, y, w)] for w in range(52)] for y in years] for z in range(len(ZONES))],
    "tot": {y: totals[y] for y in years},
}
if not data["op"]:
    sys.exit(f"None of the operational years {OPERATIONAL} are in the database. Years found: {years}")

page = (HERE / "watch_template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "watch.html").write_text(page, encoding="utf-8")

print(f"\nKept {len(keep):,} vegetation-fire detections (nominal/high confidence).")
for y in years:
    print(f"  {y}: {totals[y]:,}{'  <- operational' if y in data['op'] else ''}")
base = median(totals[y] for y in years if y not in data["op"]) if len(years) > len(data["op"]) else None
if base:
    for y in data["op"]:
        r = totals[y] / base
        if r < 0.4 or r > 2.5:
            print(f"WARNING: {y} total is {r:.1f}x the setup-year median. Check the import or sensor change before trusting alerts.")
print("\nWrote watch.html")