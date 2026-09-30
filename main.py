"""car-deal-ai-hunter — skanuje Otomoto / OLX / Autoplac i wysyła nowe oferty na Telegram.

  python main.py --dry-run     # tylko wypisz, nic nie wysyłaj i nie zapisuj do bazy
  python main.py --seed        # zapisz wszystko co jest teraz jako "widziane" (bez alertów)
  python main.py               # jeden przebieg
  python main.py --loop        # w kółko co CHECK_INTERVAL_MINUTES
"""
import argparse
import logging
import statistics
import time
from collections import Counter, defaultdict

import config
from adapters.autoplac_adapter import AutoplacAdapter
from adapters.olx_adapter import OlxAdapter
from adapters.otomoto_adapter import OtomotoAdapter
from ai_analyzer import AIAnalyzer
from database import Database
from filters import Criteria
from notifier import TelegramNotifier, format_message

ADAPTERS = {"otomoto": OtomotoAdapter, "olx": OlxAdapter, "autoplac": AutoplacAdapter}
log = logging.getLogger("main")


def market_hint(listings) -> str:
    prices = [l.price for l in listings if l.price]
    kms = [l.mileage for l in listings if l.mileage]
    if len(prices) < 5:
        return ""
    hint = f"Kontekst rynkowy (oferty pasujące do wyszukiwania): mediana ceny {int(statistics.median(prices))} zł, n={len(prices)}"
    if len(kms) >= 5:
        hint += f", mediana przebiegu {int(statistics.median(kms))} km"
    return hint + "."


def run_once(cfg, args) -> None:
    criteria = Criteria.from_dict(cfg.get("criteria", {}))
    max_pages = int(cfg.get("max_pages", 3))
    max_alerts = int(cfg.get("max_alerts_per_run", 15))

    db = Database(config.DB_PATH)
    notifier = TelegramNotifier()
    ai = AIAnalyzer()

    # grupujemy adresy wg platformy: jedna przeglądarka na platformę
    urls = defaultdict(list)
    for s in cfg["searches"]:
        if args.only and s["platform"] not in args.only:
            continue
        urls[s["platform"]].append(s["url"])

    matched = []
    for platform, plat_urls in urls.items():
        cls = ADAPTERS.get(platform)
        if not cls:
            log.error("Nieznana platforma: %s", platform)
            continue
        try:
            found = cls(headless=config.HEADLESS_MODE, max_pages=max_pages).fetch_listings(plat_urls)
        except Exception:
            log.exception("[%s] pobieranie nie powiodło się — idę dalej", platform)
            continue

        rejected = Counter()
        ok = []
        for l in found:
            passed, why = criteria.check(l)
            (ok.append(l) if passed else rejected.update([why]))
        log.info("[%s] pobrano %d, pasuje %d; odrzucone: %s", platform, len(found), len(ok), dict(rejected) or "-")
        matched += ok

    hint = market_hint(matched)
    unique = {l.uid: l for l in matched}  # ta sama oferta bywa na stronie 2x (promowana + zwykła)
    new = [l for l in unique.values() if not db.is_seen(l.uid)]
    log.info("Nowych ofert: %d (limit alertów na przebieg: %d)", len(new), max_alerts)

    sent = 0
    for l in new:
        if args.seed:
            db.add(l)
            continue
        if sent >= max_alerts:
            break  # reszta poczeka na następny przebieg — nie zalewamy czatu
        opinion = ai.analyze(l, hint)
        if args.dry_run:
            print("-" * 60)
            print(format_message(l, opinion))
            continue
        if notifier.send(l, opinion):
            db.add(l)  # zapis dopiero PO udanej wysyłce — inaczej alert przepada bezpowrotnie
            sent += 1
            time.sleep(1.5)
        else:
            log.warning("Nie wysłano %s — spróbuję w kolejnym przebiegu", l.uid)

    log.info("Koniec przebiegu. Wysłano: %d, w bazie: %d", sent, db.count())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--only", nargs="*", help="np. --only otomoto olx")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load_search_config(args.config)
    if not args.dry_run and not args.seed and not TelegramNotifier().configured:
        raise SystemExit("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID w .env (albo użyj --dry-run).")

    while True:
        try:
            run_once(cfg, args)
        except Exception:
            log.exception("Przebieg zakończony błędem")
        if not args.loop:
            break
        time.sleep(config.CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
