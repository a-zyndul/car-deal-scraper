"""Test adaptera OFFLINE na zapisanym HTML-u (bez przeglądarki i sieci).

  python tools/parse_file.py otomoto debug/otomoto_page.html
  python tools/parse_file.py olx     debug/olx_page.html
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import ADAPTERS  # noqa: E402

platform, path = sys.argv[1], sys.argv[2]
html = Path(path).read_text(encoding="utf-8", errors="ignore")
items = ADAPTERS[platform]().parse(html)
print(f"Sparsowano: {len(items)}")
for l in items[:10]:
    print(f"- {l.title[:45]:45} | {l.price} zł | {l.year} | {l.mileage} km | {l.fuel} | {l.url[:60]}")
missing = {f: sum(1 for l in items if getattr(l, f) in (None, "")) for f in ("price", "year", "mileage", "fuel")}
print("Braki pól:", missing)
