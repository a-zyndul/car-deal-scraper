import json
import logging
import math
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests
from dotenv import load_dotenv

# Konfiguracja środowiska i ścieżek
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CONFIG_PATH = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "seen_ids.json"
PROMPT_OVERRIDE_FILE = BASE_DIR / "custom_prompt.txt"
LOG_FILE = BASE_DIR / "scraper.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://100.120.167.113:11434")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logging.error(f"Brak pliku konfiguracyjnego: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_seen_ids() -> set:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data if isinstance(data, list) else data.keys())
        except Exception as e:
            logging.warning(f"Błąd odczytu {STATE_FILE}: {e}. Tworzę nową bazę.")
    return set()


def save_seen_ids(seen_ids: set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)


def get_active_prompt(config: dict) -> str:
    if PROMPT_OVERRIDE_FILE.exists():
        try:
            override = PROMPT_OVERRIDE_FILE.read_text(encoding="utf-8").strip()
            if override:
                return override
        except Exception as e:
            logging.error(f"Nie udało się odczytać custom_prompt.txt: {e}")
    return config.get("system_prompt", "Jesteś bezwzględnym mechanikiem i rzeczoznawcą samochodowym.")


# ---------------- Telegram Polling (/prompt) ----------------

def handle_telegram_updates():
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    offset_file = BASE_DIR / ".telegram_offset"
    last_offset = 0
    if offset_file.exists():
        try:
            last_offset = int(offset_file.read_text().strip())
        except ValueError:
            last_offset = 0

    try:
        resp = requests.get(url, params={"offset": last_offset + 1, "timeout": 2}, timeout=5)
        if resp.status_code != 200:
            return
        data = resp.json()
        if not data.get("ok"):
            return

        for update in data.get("result", []):
            update_id = update["update_id"]
            last_offset = max(last_offset, update_id)
            message = update.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id"))

            if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                continue

            if text.startswith("/prompt"):
                new_prompt = text[len("/prompt"):].strip()
                if new_prompt:
                    PROMPT_OVERRIDE_FILE.write_text(new_prompt, encoding="utf-8")
                    reply = f"✅ Zaktualizowano prompt systemowy AI:\n\n{new_prompt}"
                else:
                    reply = "ℹ️ Użycie: /prompt [twoje nowe wytyczne dla mechanika AI]"
                send_telegram_message(reply)

        offset_file.write_text(str(last_offset))
    except Exception as e:
        logging.debug(f"Pominięto błąd odpytywania Telegrama: {e}")


def send_telegram_message(text: str, photo_url: str = None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Brak konfiguracji Telegrama w .env")
        return

    # 1. Próba wysłania zdjęcia z podpisem
    if photo_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": photo_url,
            "caption": text[:1024],  # Limit Telegrama dla podpisu pod zdjęciem
            "parse_mode": "HTML",
        }
        try:
            r = requests.post(url, json=payload, timeout=12)
            if r.status_code == 200:
                logging.info("Wysłano powiadomienie ze zdjęciem na Telegram")
                # Jeśli wiadomość była dłuższa niż limit podpisu 1024 znaków, doślij resztę tekstu
                if len(text) > 1024:
                    send_telegram_message(text[1024:], photo_url=None)
                return
            else:
                logging.warning(f"Błąd wysyłania zdjęcia na Telegram: {r.status_code}. Fallback na tekst.")
        except Exception as e:
            logging.warning(f"Wyjątek podczas wysyłania zdjęcia: {e}. Fallback na tekst.")

    # 2. Standardowa wiadomość tekstowa
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logging.info("Wysłano powiadomienie na Telegram")
        else:
            logging.error(f"Błąd wysyłania na Telegram: {r.status_code} {r.text}")
    except Exception as e:
        logging.error(f"Błąd połączenia z Telegram API: {e}")


# ---------------- Obliczenia odległości ----------------

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c * 1.25)


# ---------------- Filtry handlarzy i komisu ----------------

def is_dealer_or_trader(listing: dict, description: str, config: dict) -> tuple[bool, str]:
    exclusions = config.get("dealer_exclusions", {})

    if exclusions.get("reject_company_sellers", True):
        user_type = str(listing.get("user", {}).get("user_type", "")).lower()
        seller_type = str(listing.get("seller_type", "")).lower()
        if user_type in ["business", "company"] or seller_type in ["business", "company"]:
            return True, f"Konto firmowe/komis (user_type={user_type or seller_type})"

    title = (listing.get("title") or "").lower()
    full_text = f"{title}\n{description.lower()}"

    banned_keywords = exclusions.get("banned_keywords", [])
    for phrase in banned_keywords:
        if phrase in full_text:
            return True, f"Wykryto wykluczoną frazę: '{phrase}'"

    return False, ""


# ---------------- Analiza AI (Ollama + Groq) ----------------

def call_ollama(prompt: str, content: str) -> str:
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": "qwen2.5:7b",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    r = requests.post(url, json=payload, timeout=12)
    if r.status_code == 200:
        data = r.json()
        return data.get("message", {}).get("content", "").strip()
    raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text}")


def call_groq(prompt: str, content: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("Brak GROQ_API_KEY w .env")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError(f"Groq HTTP {r.status_code}: {r.text}")


def analyze_with_ai(prompt: str, text: str) -> str:
    try:
        res = call_ollama(prompt, text)
        logging.info("Analiza AI: Ollama qwen2.5:7b (Tailscale)")
        return res
    except Exception as e:
        logging.warning(f"Ollama niedostępna ({e}). Przełączam na fallback Groq API...")

    try:
        res = call_groq(prompt, text)
        logging.info("Analiza AI: Groq Cloud API (Fallback)")
        return res
    except Exception as e:
        logging.error(f"Fallback Groq również zawiódł: {e}")
        return "⚠️ Błąd inferencji AI po stronie obu silników."


# ---------------- Pobieranie szczegółów i zdjęć ----------------

def clean_url(url: str) -> str:
    """Czyści parametry śledzące z linku URL."""
    return url.split("#")[0].split("?")[0]


def get_platform_name(url: str) -> str:
    """Zwraca czytelną nazwę platformy na podstawie domeny."""
    domain = urlparse(url).netloc.lower()
    if "otomoto.pl" in domain:
        return "Otomoto"
    if "olx.pl" in domain:
        return "OLX"
    return "Portal"


def fetch_listing_details(url: str, fallback_photo: str = None) -> tuple[str, str]:
    """Pobiera pełny opis ogłoszenia i bezpośredni URL 1. zdjęcia."""
    description = ""
    photo_url = fallback_photo

    try:
        r = requests.get(url, impersonate="chrome120", timeout=10)
        if r.status_code != 200:
            return "", photo_url

        soup = BeautifulSoup(r.text, "html.parser")

        # 1. Pobieranie opisu
        # Wariant OLX
        desc_div = soup.find("div", {"data-cy": "ad_description"})
        if desc_div:
            description = desc_div.get_text(separator="\n", strip=True)

        # Wariant Otomoto
        if not description:
            desc_div = soup.find("div", {"data-testid": "text-container"}) or soup.find("div", class_=re.compile("description"))
            if desc_div:
                description = desc_div.get_text(separator="\n", strip=True)

        # 2. Pobieranie pierwszego zdjęcia
        meta_img = soup.find("meta", property="og:image")
        if meta_img and meta_img.get("content"):
            photo_url = meta_img["content"]

        # Zapasowe parsowanie struktur JSON-LD lub __NEXT_DATA__
        if not photo_url or not description:
            next_data_tag = soup.find("script", id="__NEXT_DATA__")
            if next_data_tag:
                try:
                    data = json.loads(next_data_tag.string)
                    ad_data = data.get("props", {}).get("pageProps", {}).get("ad", {})
                    if not description:
                        description = ad_data.get("description", "")
                    if not photo_url:
                        photos = ad_data.get("photos", [])
                        if photos and isinstance(photos, list):
                            photo_url = photos[0].get("data", {}).get("url") or photos[0].get("url")
                except Exception:
                    pass

    except Exception as e:
        logging.error(f"Błąd pobierania detali z {url}: {e}")

    return description, photo_url


def parse_olx_listing(item: dict) -> dict:
    params = {p.get("key"): p.get("value", {}).get("label") for p in item.get("params", []) if isinstance(p, dict)}
    
    price_val = "Brak ceny"
    for p in item.get("params", []):
        if p.get("key") == "price":
            price_val = p.get("value", {}).get("label", "Brak ceny")

    raw_url = item.get("url", "")
    url = clean_url(raw_url if raw_url.startswith("http") else f"https://www.olx.pl{raw_url}")

    # Pierwsze zdjęcie z miniatury na liście OLX jako zapas
    fallback_photo = None
    photos = item.get("photos", [])
    if photos and isinstance(photos, list):
        fallback_photo = photos[0].get("link", "").replace("{width}x{height}", "1000x750")

    location_data = item.get("location", {})
    city = location_data.get("city", {}).get("name", "Nieznana")
    lat = location_data.get("latitude")
    lon = location_data.get("longitude")

    return {
        "id": str(item.get("id")),
        "title": item.get("title", ""),
        "url": url,
        "price": price_val,
        "city": city,
        "lat": lat,
        "lon": lon,
        "year": params.get("year", "Brak"),
        "mileage": params.get("milage", params.get("mileage", "Brak")),
        "engine_capacity": params.get("engine_capacity", "Brak"),
        "fuel_type": params.get("petrol", "Brak"),
        "fallback_photo": fallback_photo,
        "raw_item": item,
    }


def scan_target(target: dict, seen_ids: set, config: dict):
    url = target.get("url")
    name = target.get("name", "Pojazd")
    logging.info(f"Skanuję: {name}")

    try:
        r = requests.get(url, impersonate="chrome120", timeout=12)
        if r.status_code != 200:
            logging.error(f"HTTP {r.status_code} dla {url}")
            return

        soup = BeautifulSoup(r.text, "html.parser")
        script_tag = soup.find("script", id="__NEXT_DATA__")
        if not script_tag:
            logging.warning("Nie znaleziono tagu __NEXT_DATA__")
            return

        data = json.loads(script_tag.string)
        listing_grid = (
            data.get("props", {})
            .get("pageProps", {})
            .get("data", {})
            .get("visibleAds", [])
        )

        my_lat = config.get("my_location", {}).get("latitude")
        my_lon = config.get("my_location", {}).get("longitude")
        my_city = config.get("my_location", {}).get("city_name", "Poznań")
        prompt = get_active_prompt(config)

        for raw_item in listing_grid:
            parsed = parse_olx_listing(raw_item)
            item_id = parsed["id"]

            if not item_id or item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            save_seen_ids(seen_ids)

            title = parsed["title"]
            price = parsed["price"]
            item_url = parsed["url"]
            platform = get_platform_name(item_url)

            logging.info(f"Nowa oferta ({platform}): {title} ({price})")

            # Pobieranie pełnego opisu i 1. zdjęcia
            description, photo_url = fetch_listing_details(item_url, fallback_photo=parsed["fallback_photo"])

            # Weryfikacja filtrów komisowych/handlarskich/sprowadzanych
            is_dealer, dealer_reason = is_dealer_or_trader(parsed["raw_item"], description, config)
            if is_dealer:
                logging.info(f"Odrzucono ofertę {item_id}: {dealer_reason}")
                continue

            # Obliczenie szacunkowej odległości drogowej
            dist_str = "nieznana"
            if my_lat and my_lon and parsed["lat"] and parsed["lon"]:
                dist_km = haversine_distance(my_lat, my_lon, parsed["lat"], parsed["lon"])
                dist_str = f"~{dist_km} km od {my_city}"

            # Przygotowanie kontekstu pod analizę LLM
            analysis_payload = (
                f"Platforma: {platform}\n"
                f"Tytuł: {title}\n"
                f"Cena: {price}\n"
                f"Rocznik: {parsed['year']} | Przebieg: {parsed['mileage']}\n"
                f"Silnik: {parsed['engine_capacity']} cm3 | Paliwo: {parsed['fuel_type']}\n"
                f"Lokalizacja: {parsed['city']}\n\n"
                f"Opis sprzedawcy:\n{description[:2500]}"
            )

            ai_summary = analyze_with_ai(prompt, analysis_payload)

            # Formatowanie wiadomości Telegram z dynamiczną nazwą portalu
            msg = (
                f"🚗 <b>TRAFIENIE: {title}</b>\n\n"
                f"💰 <b>Cena:</b> {price}\n"
                f"📅 <b>Rocznik:</b> {parsed['year']} | 🛣 <b>Przebieg:</b> {parsed['mileage']}\n"
                f"📍 <b>Lokalizacja:</b> {parsed['city']} ({dist_str})\n"
                f"🔗 <a href='{item_url}'>Otwórz ogłoszenie na {platform}</a>\n\n"
                f"🤖 <b>ANALIZA MECHANIKA (AI):</b>\n{ai_summary}"
            )

            send_telegram_message(msg, photo_url=photo_url)
            time.sleep(random.uniform(2.0, 4.0))

    except Exception as e:
        logging.error(f"Błąd podczas parsowania targetu {name}: {e}")


def main():
    config = load_config()
    seen_ids = load_seen_ids()
    logging.info(f"Uruchomiono scraper. W bazie: {len(seen_ids)} ogłoszeń.")

    # Sprawdzenie ewentualnych komend /prompt z Telegrama
    handle_telegram_updates()

    targets = config.get("search_targets", [])
    for target in targets:
        scan_target(target, seen_ids, config)
        time.sleep(random.uniform(3.0, 6.0))


if __name__ == "__main__":
    main()