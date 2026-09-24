import os
import re
import json
import time
import math
import random
import logging
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
from curl_cffi import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://100.120.167.113:11344")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

SEEN_IDS_FILE = os.path.expanduser("~/scraper/seen_ids.json")
CONFIG_FILE = os.path.expanduser("~/scraper/config.json")
LOCK_FILE = os.path.expanduser("~/scraper/.scraper.lock")


def clean_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def get_platform_name(url: str) -> str:
    if "otomoto.pl" in url:
        return "Otomoto"
    return "OLX"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return int(round(r * c))


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_seen_ids() -> set:
    if not os.path.exists(SEEN_IDS_FILE):
        return set()
    try:
        with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_seen_ids(seen_ids: set):
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)


def is_dealer_or_trader(item_dict: dict, description: str, config: dict) -> tuple[bool, str]:
    exclusions = config.get("dealer_exclusions", {})
    if exclusions.get("reject_company_sellers", True):
        user_info = item_dict.get("user", {})
        if user_info.get("company_name") or user_info.get("is_business"):
            return True, "Konto firmowe / komis"

    full_text = f"{item_dict.get('title', '')} {description}".lower()
    banned_keywords = exclusions.get("banned_keywords", [])

    for kw in banned_keywords:
        if kw.lower() in full_text:
            return True, f"Wykryto frazę handlarską: '{kw}'"

    return False, ""


def fetch_listing_details(url: str, fallback_photo: str = None) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, impersonate="chrome120", timeout=12)
        if r.status_code != 200:
            return "Brak opisu", fallback_photo

        soup = BeautifulSoup(r.text, "html.parser")
        description = ""

        # Opis OLX
        desc_div = soup.find("div", {"data-cy": "ad_description"})
        if desc_div:
            description = desc_div.get_text(separator="\n", strip=True)

        # Opis Otomoto
        if not description:
            desc_sec = soup.find("div", {"data-read-more": "true"}) or soup.find("section", id="description")
            if desc_sec:
                description = desc_sec.get_text(separator="\n", strip=True)

        # Szukanie 1. zdjęcia (najwyższa jakość z ogłoszenia)
        photo_url = fallback_photo
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            photo_url = og_img["content"]

        return description or "Brak opisu", photo_url
    except Exception as e:
        logging.warning(f"Błąd pobierania detali {url}: {e}")
        return "Brak opisu", fallback_photo


def analyze_with_ai(system_prompt: str, user_payload: str) -> str:
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload}
        ]
    }
    try:
        logging.info(f"Analiza AI: Ollama {OLLAMA_MODEL} (Tailscale)")
        r = requests.post(url, json=payload, timeout=90)
        if r.status_code == 200:
            data = r.json()
            return data.get("message", {}).get("content", "").strip()
        else:
            logging.error(f"Błąd Ollama HTTP {r.status_code}: {r.text}")
    except Exception as e:
        logging.error(f"Wyjątek podczas komunikacji z Ollamą: {e}")
    return "Nie udało się wygenerować analizy AI."


def send_telegram_message(caption: str, photo_url: str = None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Brak tokenów Telegrama w .env")
        return

    # Jeśli mamy zdjęcie, wysyłamy jako sendPhoto
    if photo_url:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption[:1024],
            "parse_mode": "HTML"
        }
        try:
            r = requests.post(send_url, data=data, json={"photo": photo_url}, timeout=15)
            if r.status_code == 200:
                logging.info("Wysłano powiadomienie ze zdjęciem na Telegram")
                return
            else:
                logging.warning(f"Telegram photo error ({r.status_code}), próba tekstem...")
        except Exception as e:
            logging.warning(f"Błąd wysyłki zdjęcia: {e}")

    # Fallback na zwykły sendMessage
    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": caption[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(send_url, json=payload, timeout=10)
        if r.status_code == 200:
            logging.info("Wysłano powiadomienie na Telegram")
        else:
            logging.error(f"Telegram error: {r.text}")
    except Exception as e:
        logging.error(f"Błąd połączenia z Telegramem: {e}")


def scan_target(target: dict, seen_ids: set, config: dict):
    url = target.get("url")
    name = target.get("name", "Pojazd")
    logging.info(f"Skanuję: {name}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
    }

    try:
        r = requests.get(url, headers=headers, impersonate="chrome120", timeout=15)
        if r.status_code != 200:
            logging.error(f"HTTP {r.status_code} dla {url}")
            return

        soup = BeautifulSoup(r.text, "html.parser")
        listing_grid = []

        # 1. Próba z tagu __NEXT_DATA__
        script_tag = soup.find("script", id="__NEXT_DATA__")
        if script_tag and script_tag.string:
            try:
                data = json.loads(script_tag.string)
                listing_grid = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("data", {})
                    .get("visibleAds", [])
                )
            except Exception as e:
                logging.warning(f"Błąd parsowania __NEXT_DATA__: {e}")

        # 2. Fallback: Parsowanie HTML z twardym filtrem motoryzacji
        if not listing_grid:
            main_listing = soup.find("div", {"data-testid": "listing-grid"})
            cards = main_listing.find_all("div", {"data-cy": "l-card"}) if main_listing else soup.find_all("div", {"data-cy": "l-card"})

            for card in cards:
                link_tag = card.find("a", href=True)
                if not link_tag:
                    continue
                ad_url = link_tag["href"]
                if not ad_url.startswith("http"):
                    ad_url = f"https://www.olx.pl{ad_url}"

                # FILTR ANTY-WAZONOWY: Dopuszczamy wyłącznie kategorie motoryzacji lub otomoto
                lower_url = ad_url.lower()
                is_car = ("otomoto.pl" in lower_url) or ("/motoryzacja/" in lower_url) or ("/samochody/" in lower_url)
                if not is_car:
                    continue

                title_tag = card.find("h6") or card.find("h4")
                title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

                price_tag = card.find("p", {"data-testid": "ad-price"})
                price = price_tag.get_text(strip=True) if price_tag else "Brak ceny"

                ad_id = card.get("id")
                if not ad_id:
                    match = re.search(r"-ID([a-zA-Z0-9]+)\.html", ad_url)
                    ad_id = match.group(1) if match else clean_url(ad_url)

                img_tag = card.find("img")
                photo_url = img_tag.get("src") or img_tag.get("data-src") if img_tag else None

                listing_grid.append({
                    "id": str(ad_id),
                    "title": title,
                    "url": ad_url,
                    "price_str": price,
                    "photo_url": photo_url,
                    "is_html_fallback": True
                })

        if not listing_grid:
            logging.info("Brak ogłoszeń na liście.")
            return

        my_lat = config.get("location", {}).get("home_lat") or config.get("my_location", {}).get("latitude")
        my_lon = config.get("location", {}).get("home_lon") or config.get("my_location", {}).get("longitude")
        my_city = config.get("location", {}).get("home_city") or config.get("my_location", {}).get("city_name", "Poznań")
        system_prompt = config.get("system_prompt", "Jesteś mechanikiem. Oceń auto.")

        for item in listing_grid:
            if item.get("is_html_fallback"):
                item_id = item["id"]
                item_url = clean_url(item["url"])
                title = item["title"]
                price = item["price_str"]
                fallback_photo = item["photo_url"]
                item_dict_for_filter = {"title": title}
                year = mileage = engine = fuel = "Brak danych"
                city = "Polska"
                lat = lon = None
            else:
                item_id = str(item.get("id"))
                item_url = clean_url(item.get("url", ""))
                title = item.get("title", "")
                price_data = item.get("price", {})
                price = price_data.get("displayValue") or f"{price_data.get('value')} zł"
                params = {p.get("key"): p.get("value", {}).get("label") for p in item.get("params", []) if p.get("key")}
                year = params.get("year", "Brak")
                mileage = params.get("milage", "Brak")
                engine = params.get("engine_capacity", "Brak")
                fuel = params.get("petrol", "Brak")
                loc = item.get("location", {})
                city = loc.get("city", {}).get("name", "Polska")
                lat = loc.get("latitude")
                lon = loc.get("longitude")
                photos = item.get("photos", [])
                fallback_photo = photos[0].get("link", "").replace("{width}x{height}", "1000x750") if photos else None
                item_dict_for_filter = item

            if not item_id or item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            save_seen_ids(seen_ids)

            platform = get_platform_name(item_url)

            # Pobranie opisu i lepszego zdjęcia
            description, photo_url = fetch_listing_details(item_url, fallback_photo=fallback_photo)

            is_dealer, dealer_reason = is_dealer_or_trader(item_dict_for_filter, description, config)
            if is_dealer:
                logging.info(f"Odrzucono ofertę ({dealer_reason}): {title}")
                continue

            logging.info(f"Nowa oferta ({platform}): {title} ({price})")

            dist_str = "nieznana"
            if my_lat and my_lon and lat and lon:
                dist_km = haversine_distance(my_lat, my_lon, lat, lon)
                dist_str = f"~{dist_km} km od {my_city}"

            analysis_payload = (
                f"Platforma: {platform}\n"
                f"Tytuł: {title}\n"
                f"Cena: {price}\n"
                f"Rocznik: {year} | Przebieg: {mileage}\n"
                f"Silnik: {engine} cm3 | Paliwo: {fuel}\n"
                f"Lokalizacja: {city}\n\n"
                f"Opis ogłoszenia:\n{description[:2500]}"
            )

            ai_summary = analyze_with_ai(system_prompt, analysis_payload)

            msg = (
                f"🚗 <b>TRAFIENIE: {title}</b>\n\n"
                f"💰 <b>Cena:</b> {price}\n"
                f"📅 <b>Rocznik:</b> {year} | 🛣 <b>Przebieg:</b> {mileage}\n"
                f"📍 <b>Lokalizacja:</b> {city} ({dist_str})\n"
                f"🔗 <a href='{item_url}'>Otwórz ogłoszenie na {platform}</a>\n\n"
                f"🤖 <b>ANALIZA MECHANIKA (AI):</b>\n{ai_summary}"
            )

            send_telegram_message(msg, photo_url=photo_url)
            time.sleep(random.uniform(2.0, 4.0))

    except Exception as e:
        logging.error(f"Błąd podczas parsowania targetu {name}: {e}")


def main():
    if os.path.exists(LOCK_FILE):
        try:
            lock_age = time.time() - os.path.getmtime(LOCK_FILE)
            if lock_age < 300:
                logging.info("Inna instancja scrapera już działa. Pomijam.")
                return
        except Exception:
            pass

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        config = load_config()
        targets = config.get("search_targets") or config.get("targets", [])
        seen_ids = load_seen_ids()

        logging.info(f"Uruchomiono scraper. W bazie: {len(seen_ids)} ogłoszeń.")

        for target in targets:
            scan_target(target, seen_ids, config)
    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == "__main__":
    main()