"""Scraper ogłoszeń OLX: filtruje nowe oferty, dodaje analizę AI i wysyła alert na Telegram.

Narzędzie w architekturze hybrydowej (Local GPU via Tailscale -> Cloud Groq Fallback).
Konfiguracja dynamiczna z pliku config.json oraz środowiskowa z .env.
"""
import html
import json
import logging
import math
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from bs4 import BeautifulSoup
from curl_cffi import requests
from dotenv import load_dotenv

try:
    import ollama
except ImportError:
    ollama = None

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import fcntl
except ImportError:
    fcntl = None

# ============================================================
# INICJALIZACJA I ŚRODOWISKO
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scraper")

CONFIG_FILE = BASE_DIR / "config.json"
DB_FILE = BASE_DIR / "seen_cars.jsonl"
STATE_FILE = BASE_DIR / "scraper_state.json"
LOCK_FILE = BASE_DIR / ".scraper.lock"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

USE_OLLAMA = os.getenv("USE_OLLAMA", "0") == "1"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "12"))

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "2000"))

groq_client = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None

DETAIL_FAIL_LIMIT = 3
HEALTH_ALERT_AFTER = 6
IMPERSONATE = "chrome120"


# ============================================================
# ŁADOWANIE I ZAPIS KONFIGURACJI
# ============================================================
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Brak pliku konfiguracyjnego: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CONFIG = load_config()
HOME_LAT = CONFIG.get("location", {}).get("home_lat", 52.4064)
HOME_LON = CONFIG.get("location", {}).get("home_lon", 16.9252)
HOME_CITY = CONFIG.get("location", {}).get("home_city", "Poznań")

FORBIDDEN_WORDS = CONFIG.get("forbidden_words", [])
FORBIDDEN_RE = re.compile(
    r"(?<![^\W\d_])(?:" + "|".join(re.escape(w) for w in FORBIDDEN_WORDS) + r")(?![^\W\d_])",
    re.IGNORECASE,
)
SUS_PHRASES = CONFIG.get("sus_phrases", [])

CITY_COORDS_CACHE: dict[str, tuple[float, float]] = {
    HOME_CITY.lower(): (HOME_LAT, HOME_LON),
    "warszawa": (52.2297, 21.0122),
    "wrocław": (51.1079, 17.0385),
    "kraków": (50.0647, 19.9450),
    "łódź": (51.7592, 19.4560),
    "gdańsk": (54.3520, 18.6466),
    "szczecin": (53.4285, 14.5528),
    "bydgoszcz": (53.1235, 18.0084),
    "toruń": (53.0138, 18.5984),
    "sosnowiec": (50.2863, 19.1041),
    "rawicz": (51.6095, 16.8578),
    "więcbork": (53.3514, 17.4891),
    "ostrołęka": (53.0847, 21.5746),
    "tarnów": (50.0121, 20.9858),
    "mogilno": (52.6575, 17.9547),
    "margonin": (52.9717, 17.0944),
}


# ============================================================
# ZARZĄDZANIE PROMPTEM PRZEZ TELEGRAM
# ============================================================
def check_telegram_commands(state: dict) -> None:
    """Sprawdza czy użytkownik nie wysłał komendy /prompt na Telegramie."""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    last_update_id = state.get("last_tg_update_id", 0)
    try:
        res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 2}, timeout=5)
        if res.status_code == 200:
            updates = res.json().get("result", [])
            for u in updates:
                state["last_tg_update_id"] = u["update_id"]
                msg = u.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id"))
                text = msg.get("text", "").strip()

                if chat_id == str(TELEGRAM_CHAT_ID):
                    if text.startswith("/prompt "):
                        new_prompt = text.replace("/prompt ", "", 1).strip()
                        CONFIG["ai"]["system_prompt"] = new_prompt
                        save_config(CONFIG)
                        tg_send(f"✅ <b>Zaktualizowano prompt systemowy AI:</b>\n<i>{html.escape(new_prompt)}</i>")
                    elif text == "/prompt":
                        curr = CONFIG["ai"]["system_prompt"]
                        tg_send(f"ℹ️ <b>Aktualny prompt AI:</b>\n<i>{html.escape(curr)}</i>\n\nAby zmienić: <code>/prompt [nowa treść]</code>")
    except Exception as e:
        log.debug("Nie udało się pobrać aktualizacji Telegrama: %s", e)


# ============================================================
# GENEROWANIE ADRESU URL
# ============================================================
def build_url(target: dict) -> str:
    base = f"https://www.olx.pl/motoryzacja/samochody/q-{target['olx_slug']}/"
    params = [
        f"search%5Bfilter_float_price:from%5D={target.get('price_min', 0)}",
        f"search%5Bfilter_float_price:to%5D={target.get('price_max', 1000000)}",
        f"search%5Bfilter_float_year:from%5D={target.get('year_min', 2000)}",
        f"search%5Bfilter_float_milage:to%5D={target.get('mileage_max', 500000)}",
        "search%5Border%5D=created_at:desc"
    ]
    for idx, fuel in enumerate(target.get("fuel_types", [])):
        params.append(f"search%5Bfilter_enum_petrol%5D%5B{idx}%5D={fuel}")
    return base + "?" + "&".join(params)


# ============================================================
# OBLICZANIE ODLEGŁOŚCI
# ============================================================
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def get_distance_from_home(location_raw: str) -> Optional[int]:
    clean_city = location_raw.split("-")[0].strip()
    clean_city = clean_city.split(",")[0].strip().lower()
    if not clean_city:
        return None

    if clean_city in CITY_COORDS_CACHE:
        lat, lon = CITY_COORDS_CACHE[clean_city]
        dist_air = haversine_km(HOME_LAT, HOME_LON, lat, lon)
        return int(dist_air * 1.25)

    try:
        url = f"https://nominatim.openstreetmap.org/search?q={clean_city},Poland&format=json&limit=1"
        headers = {"User-Agent": "PersonalCarScraper/1.0"}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200 and r.json():
            data = r.json()[0]
            lat = float(data["lat"])
            lon = float(data["lon"])
            CITY_COORDS_CACHE[clean_city] = (lat, lon)
            dist_air = haversine_km(HOME_LAT, HOME_LON, lat, lon)
            return int(dist_air * 1.25)
    except Exception:
        pass
    return None


# ============================================================
# PARSOWANIE ROCZNIKA, PRZEBIEGU I CEPIK
# ============================================================
MONTHS_PL = {
    "stycznia": "01", "lutego": "02", "marca": "03", "kwietnia": "04",
    "maja": "05", "czerwca": "06", "lipca": "07", "sierpnia": "08",
    "września": "09", "października": "10", "listopada": "11", "grudnia": "12",
}

VIN_CHARS = r"[A-HJ-NPR-Z0-9]"
VIN_PLAIN_RE = re.compile(rf"\b{VIN_CHARS}{{17}}\b")
VIN_SPACED_RE = re.compile(rf"VIN[:\s\-]+((?:{VIN_CHARS}[\s\-]?){{17}})")
DATE_LABELED_RE = re.compile(
    r"(?:\b1\.?\s*rej\w*|pierwsz\w+\s+rejestracj\w*|data\s+(?:pierwszej\s+)?rejestracji)"
    r"[^\d]{0,25}(\d{1,2}[\s.\-/](?:[a-ząćęłńóśźż]+|\d{1,2})[\s.\-/]\d{4})",
    re.IGNORECASE,
)
PLATE_LABELED_RE = re.compile(
    r"(?i:nr\.?\s*rej\w*|numer\s+rejestracyjn\w*|tablice|blachy)"
    r"[^\w]{0,5}([A-Z]{2,3}\s?[A-Z0-9]{4,5})\b"
)


def _valid_vin(vin: str) -> bool:
    return (
        len(vin) == 17
        and re.fullmatch(rf"{VIN_CHARS}{{17}}", vin) is not None
        and any(c.isdigit() for c in vin)
        and any(c.isalpha() for c in vin)
    )


def normalize_date(value: str) -> Optional[str]:
    val = value.strip().lower()
    m_words = re.search(r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", val)
    if m_words:
        day, month_str, year = m_words.groups()
        month = MONTHS_PL.get(month_str)
        if month:
            return f"{int(day):02d}.{month}.{year}"

    val_clean = re.sub(r"[\-/]", ".", val)
    m_num = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", val_clean)
    if m_num:
        d, m, y = m_num.groups()
        return f"{int(d):02d}.{int(m):02d}.{y}"

    return None


def extract_key_metrics(details: dict, raw_html: str, description: str, title: str, ai_text: str = "") -> tuple[str, str, Optional[int]]:
    year = None
    mileage_str = None
    mileage_num = None

    clean_html = raw_html.replace("\xa0", " ")

    for k, v in details.items():
        k_low = str(k).lower().strip()
        v_str = str(v).replace("\xa0", " ").strip()

        if not year and ("rok" in k_low or "year" in k_low) and "rejestr" not in k_low:
            m = re.search(r"\b(201[2-9]|202[0-6])\b", v_str)
            if m:
                year = m.group(1)

        if not mileage_str and ("przebieg" in k_low or "mileage" in k_low):
            m = re.search(r"(\d[\d\s]{2,8})", v_str)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if digits and int(digits) > 500:
                    mileage_num = int(digits)
                    mileage_str = f"{mileage_num:,}".replace(",", " ") + " km"

    if not year:
        m_used = re.search(r"Używany\s*·\s*(\d{4})", clean_html)
        if m_used and 2012 <= int(m_used.group(1)) <= 2026:
            year = m_used.group(1)

    if not mileage_str:
        m_oto = re.search(r"(\d[\d\s]{2,8})\s*km[\s\S]{0,30}?(?:Przebieg|przebieg)", clean_html)
        if m_oto:
            digits = re.sub(r"\D", "", m_oto.group(1))
            if digits and int(digits) > 500:
                mileage_num = int(digits)
                mileage_str = f"{mileage_num:,}".replace(",", " ") + " km"

    if not mileage_str:
        m_t = re.search(r"(\d{2,3})\s*tys(?:\.|\b)?\s*km", title, re.IGNORECASE)
        if m_t:
            num = int(m_t.group(1)) * 1000
            mileage_num = num
            mileage_str = f"{num:,}".replace(",", " ") + " km"
        else:
            m_t2 = re.search(r"(\d{2,3}[\s\.]\d{3})\s*km", title, re.IGNORECASE)
            if m_t2:
                digits = re.sub(r"\D", "", m_t2.group(1))
                mileage_num = int(digits)
                mileage_str = f"{mileage_num:,}".replace(",", " ") + " km"

    if not mileage_str:
        m_json = re.search(r'["\']mileage["\']:\s*["\']?(\d+)', clean_html)
        if m_json and int(m_json.group(1)) > 500:
            mileage_num = int(m_json.group(1))
            mileage_str = f"{mileage_num:,}".replace(",", " ") + " km"

    if not mileage_str and ai_text:
        m_ai = re.search(r"(\d{2,3}[\s\xa0]?\d{3})\s*km", ai_text, re.IGNORECASE)
        if m_ai:
            digits = re.sub(r"\D", "", m_ai.group(1))
            if digits and int(digits) > 500:
                mileage_num = int(digits)
                mileage_str = f"{mileage_num:,}".replace(",", " ") + " km"

    if not mileage_str:
        m_desc = re.search(r"(?:przebieg[u:\s]*|przejechane\s*)(\d[\d\s]{2,7})\s*(?:tys\.?\s*)?km", description, re.IGNORECASE)
        if m_desc:
            raw_str = re.sub(r"\D", "", m_desc.group(1))
            if raw_str:
                num = int(raw_str)
                if "tys" in m_desc.group(0).lower() and num < 1000:
                    num *= 1000
                if num > 1000:
                    mileage_num = num
                    mileage_str = f"{num:,}".replace(",", " ") + " km"

    return year or "Brak", mileage_str or "Brak w ogłoszeniu", mileage_num


def check_red_flags(year_str: str, mileage_num: Optional[int], title: str, description: str) -> list[str]:
    flags = []
    text_corpus = f"{title} {description}".lower()

    found_phrases = [p for p in SUS_PHRASES if p in text_corpus]
    if found_phrases:
        flags.append(f"Handlarski opis ({', '.join(found_phrases[:2])})")

    if year_str.isdigit() and mileage_num:
        age = max(1, 2026 - int(year_str))
        km_per_year = mileage_num / age
        if km_per_year < 9000 and mileage_num < 110000:
            flags.append(f"Podejrzanie niski przebieg (~{int(km_per_year):,} km/rok)")

    return flags


def extract_cepik_data(details: dict, raw_html: str, description: str) -> dict:
    data = {"vin": None, "registration": None, "first_reg_date": None}

    for key, value in details.items():
        k = key.lower().strip()
        v = str(value).strip()

        if any(term in k for term in ["pierwsza rejestracja", "data pierwszej", "first_registration_date"]):
            norm_d = normalize_date(v)
            if norm_d:
                data["first_reg_date"] = norm_d
        elif any(term in k for term in ["numer rejestracyjny", "nr rejestracyjny", "tablica"]):
            plate = re.sub(r"\s+", "", v).upper()
            if 7 <= len(plate) <= 8:
                data["registration"] = plate
        elif k in ("vin", "numer vin", "vehicleidentificationnumber"):
            vin = re.sub(r"[\s\-]+", "", v).upper()
            if _valid_vin(vin):
                data["vin"] = vin

    if not data["vin"]:
        m_vin = re.search(r'(?:Numer VIN|VIN)[:"\s]+(?:value":")?([A-HJ-NPR-Z0-9]{17})', raw_html, re.IGNORECASE)
        if m_vin and _valid_vin(m_vin.group(1)):
            data["vin"] = m_vin.group(1).upper()

    text = description.replace("\xa0", " ")
    if not data["vin"]:
        upper = text.upper()
        for m in VIN_PLAIN_RE.finditer(upper):
            if _valid_vin(m.group()):
                data["vin"] = m.group()
                break
        else:
            m = VIN_SPACED_RE.search(upper)
            if m:
                vin = re.sub(r"[\s\-]", "", m.group(1))
                if _valid_vin(vin):
                    data["vin"] = vin

    if not data["first_reg_date"]:
        m = DATE_LABELED_RE.search(text)
        if m:
            norm_d = normalize_date(m.group(1))
            if norm_d:
                data["first_reg_date"] = norm_d

    if not data["registration"]:
        m = PLATE_LABELED_RE.search(text)
        if m:
            plate = re.sub(r"\s+", "", m.group(1)).upper()
            if 7 <= len(plate) <= 8:
                data["registration"] = plate

    return data


# ============================================================
# POBIERANIE SZCZEGÓŁÓW
# ============================================================
def get_ad_details(url: str) -> Optional[tuple[dict, str, str]]:
    try:
        res = requests.get(url, impersonate=IMPERSONATE, timeout=15)
    except Exception as e:
        log.warning("Wyjątek sieciowy przy %s: %s", url, e)
        return None

    if res.status_code != 200:
        log.warning("HTTP %s przy pobieraniu szczegółów: %s", res.status_code, url)
        return None

    raw_html = res.text
    soup = BeautifulSoup(raw_html, "html.parser")
    details: dict = {}
    description = ""

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            js = json.loads(script.string)
            if isinstance(js, list) and js:
                js = js[0]
            if isinstance(js, dict):
                if js.get("description") and not description:
                    description = js["description"]
                if js.get("vehicleIdentificationNumber"):
                    details["VIN"] = js["vehicleIdentificationNumber"]
        except Exception:
            continue

    for s_tag in soup.find_all("script"):
        txt = s_tag.string or ""
        if "__NEXT_DATA__" in str(s_tag.get("id", "")) or "window.__PRERENDERED_STATE__" in txt:
            try:
                json_str = txt
                if "window.__PRERENDERED_STATE__" in txt:
                    m = re.search(r"window\.__PRERENDERED_STATE__\s*=\s*(\{.*?\});", txt)
                    if m:
                        json_str = m.group(1)
                nd = json.loads(json_str)

                def find_params(d):
                    if isinstance(d, dict):
                        if "parameters" in d and isinstance(d["parameters"], list):
                            for p in d["parameters"]:
                                k = p.get("key") or p.get("name") or p.get("label")
                                v = p.get("value_label") or p.get("value")
                                if k and v:
                                    details[str(k)] = str(v)
                        for val in d.values():
                            find_params(val)
                    elif isinstance(d, list):
                        for item in d:
                            find_params(item)

                find_params(nd)
            except Exception:
                pass

    for item in soup.find_all("div", attrs={"data-testid": ["main-details-item", "advert-details-item"]}):
        paras = item.find_all(["p", "span", "div"])
        if len(paras) >= 2:
            details[paras[0].get_text(strip=True)] = paras[1].get_text(strip=True)
            details[paras[1].get_text(strip=True)] = paras[0].get_text(strip=True)

    for tag in soup.find_all(["p", "li", "span"]):
        txt = tag.get_text(separator=" ", strip=True)
        if any(prefix in txt for prefix in ["Rok produkcji:", "Przebieg:", "Poj. silnika:", "Paliwo:", "Numer VIN:", "Moc silnika:"]):
            if ":" in txt:
                k, v = txt.split(":", 1)
                details[k.strip()] = v.strip()

    if not description:
        el = soup.find("div", {"data-cy": "ad_description"}) or soup.find("div", {"data-testid": "ad-description"})
        if el:
            description = el.get_text(separator="\n", strip=True)

    return details, description[:3000], raw_html


# ============================================================
# ANALIZA AI (Tailscale Ollama -> Cloud Groq Fallback)
# ============================================================
def build_user_prompt(title, price, location, details, description) -> str:
    template = CONFIG.get("ai", {}).get("user_prompt_template", "")
    details_str = "\n".join(f"- {k}: {v}" for k, v in details.items() if v)
    return template.format(
        title=title,
        price=price,
        location=location,
        details=details_str,
        description=description
    )


def analyze_ollama(user_prompt: str) -> str:
    system_prompt = CONFIG.get("ai", {}).get("system_prompt", "")
    client = ollama.Client(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT)
    res = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.2, "repeat_penalty": 1.2},
    )
    return res["message"]["content"]


def analyze_groq(model: str, user_prompt: str) -> str:
    system_prompt = CONFIG.get("ai", {}).get("system_prompt", "")
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_completion_tokens=GROQ_MAX_TOKENS,
    )
    if "gpt-oss" in model:
        kwargs["reasoning_effort"] = "low"

    try:
        completion = groq_client.chat.completions.create(**kwargs)
    except TypeError:
        kwargs.pop("reasoning_effort", None)
        kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
        completion = groq_client.chat.completions.create(**kwargs)

    choice = completion.choices[0]
    text = (choice.message.content or "").strip()
    if not text:
        raise RuntimeError(f"pusta odpowiedź (finish_reason={choice.finish_reason})")
    return text


def get_ai_analysis(title, price, location, details, description) -> str:
    user_prompt = build_user_prompt(title, price, location, details, description)

    backends: list[tuple[str, Callable[[], str]]] = []
    if USE_OLLAMA and ollama:
        backends.append((f"Ollama {OLLAMA_MODEL} (Tailscale)", lambda: analyze_ollama(user_prompt)))
    if groq_client:
        backends.append((f"Groq {GROQ_MODEL}", lambda: analyze_groq(GROQ_MODEL, user_prompt)))
        if GROQ_FALLBACK_MODEL and GROQ_FALLBACK_MODEL != GROQ_MODEL:
            backends.append(
                (f"Groq {GROQ_FALLBACK_MODEL}", lambda: analyze_groq(GROQ_FALLBACK_MODEL, user_prompt))
            )

    for name, run in backends:
        try:
            text = (run() or "").strip()
            if text:
                log.info("Analiza AI: %s", name)
                return text
            log.warning("%s zwrócił pustą odpowiedź", name)
        except Exception as e:
            log.warning("%s niedostępny, sprawdzam zapasowy backend: %s", name, e)

    return ""


# ============================================================
# TELEGRAM
# ============================================================
def tg_send(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID w .env")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code == 200:
            return True
        log.warning("Telegram HTTP %s: %s", res.status_code, res.text[:200])

        payload.pop("parse_mode")
        payload["text"] = html.unescape(re.sub(r"<[^>]+>", "", text))
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code == 200:
            return True
        log.error("Telegram (fallback) HTTP %s: %s", res.status_code, res.text[:200])
    except Exception as e:
        log.error("Błąd Telegrama: %s", e)
    return False


def build_message(title, price, location, link, cepik, ai_analysis, year, mileage, distance_km, red_flags: list[str]) -> str:
    e = html.escape
    ai_block = e(ai_analysis[:1500]) if ai_analysis else "brak (AI niedostępne)"
    dist_str = f" (~{distance_km} km od {HOME_CITY})" if distance_km is not None else ""

    score = 0
    m_score = re.search(r"OCENA:\s*(\d{1,2})/10", ai_analysis or "")
    if m_score:
        score = int(m_score.group(1))

    if score >= 8:
        header = f"🔥 <b>PEREŁKA / OKAZJA ({score}/10)</b> 🔥\n🚗 <b>TRAFIENIE:</b> {e(title)}"
    else:
        header = f"🚗 <b>TRAFIENIE:</b> {e(title)}"

    warning_block = ""
    if red_flags:
        warning_block = "🚨 <b>CZERWONA LAMPKA:</b>\n" + "\n".join(f"⚠️ <i>{e(f)}</i>" for f in red_flags) + "\n\n"

    msg = (
        f"{header}\n\n"
        f"{warning_block}"
        f"📅 <b>Rocznik:</b> {e(year)} | 🛣️ <b>Przebieg:</b> {e(mileage)}\n"
        f"💰 <b>Cena:</b> {e(price)}\n"
        f"📍 <b>Lokalizacja:</b> {e(location)}{dist_str}\n\n"
    )

    cepik_lines = []
    if cepik.get("vin"):
        cepik_lines.append(f"• <b>VIN:</b> <code>{e(cepik['vin'])}</code>")
    if cepik.get("registration"):
        cepik_lines.append(f"• <b>Nr rej:</b> <code>{e(cepik['registration'])}</code>")
    if cepik.get("first_reg_date"):
        cepik_lines.append(f"• <b>Data 1. rej:</b> <code>{e(cepik['first_reg_date'])}</code>")

    if cepik_lines:
        msg += "🔍 <b>DANE DO CEPIK:</b>\n" + "\n".join(cepik_lines) + "\n"
        msg += '👉 <a href="https://historiapojazdu.gov.pl">historiapojazdu.gov.pl</a>\n\n'

    msg += f"🤖 <b>ANALIZA MECHANIKA (AI):</b>\n{ai_block}\n\n"
    msg += f'🔗 <a href="{e(link, quote=True)}">Zobacz ogłoszenie na OLX</a>'
    return msg


# ============================================================
# BAZA I STAN
# ============================================================
def load_seen_ids() -> set:
    if not DB_FILE.exists():
        return set()
    seen = set()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    seen.add(json.loads(line)["id"])
                except Exception:
                    continue
    return seen


def save_seen(offer_id: str, title: str, price: str, decision: str) -> None:
    record = {
        "id": offer_id,
        "title": title,
        "price": price,
        "decision": decision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_id(url: str) -> str:
    match = re.search(r"-ID([a-zA-Z0-9]+)", url)
    return match.group(1) if match else url


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        log.warning("Nie udało się zapisać stanu: %s", e)


def report_health(state: dict, name: str, ok: bool, reason: str = "") -> None:
    if ok:
        state[name] = 0
        return
    state[name] = state.get(name, 0) + 1
    if state[name] == HEALTH_ALERT_AFTER:
        tg_send(
            f"⚠️ <b>Scraper:</b> {html.escape(name)} — {HEALTH_ALERT_AFTER} nieudanych skanów z rzędu "
            f"({html.escape(reason)}). Sprawdź selektory OLX lub blokadę IP."
        )


def acquire_lock():
    if fcntl is None:
        return True
    handle = open(LOCK_FILE, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


# ============================================================
# SKANOWANIE MODELU
# ============================================================
def scan_target(target: dict, seen_ids: set, state: dict) -> None:
    name = target["name"]
    url = build_url(target)
    filters = target.get("filters", {})
    mileage_max = target.get("mileage_max", 500000)

    log.info("Skanuję: %s", name)

    try:
        res = requests.get(url, impersonate=IMPERSONATE, timeout=15)
    except Exception as e:
        log.warning("Błąd połączenia z OLX: %s", e)
        report_health(state, name, False, f"błąd połączenia: {e}"[:80])
        return

    if res.status_code != 200:
        log.warning("OLX zwrócił HTTP %s dla %s", res.status_code, name)
        report_health(state, name, False, f"HTTP {res.status_code}")
        return

    soup = BeautifulSoup(res.text, "html.parser")
    offers = soup.find_all("div", {"data-cy": "l-card"})
    if not offers:
        log.warning("Brak kart ogłoszeń dla %s (zmiana układu strony lub brak wyników)", name)
        report_health(state, name, False, "0 ogłoszeń na stronie")
        return
    report_health(state, name, True)

    detail_failures = 0

    for offer in offers:
        title_el = offer.find("h4")
        price_el = offer.find("p", {"data-testid": "ad-price"})
        link_el = offer.find("a")
        loc_el = offer.find("p", {"data-testid": "location-date"})

        if not (title_el and price_el and link_el):
            continue

        link = link_el.get("href") or ""
        if link.startswith("/"):
            link = f"https://www.olx.pl{link}"
        if not link:
            continue

        offer_id = extract_id(link)
        if offer_id in seen_ids:
            continue

        title = title_el.get_text(strip=True)
        price = price_el.get_text(strip=True)
        location = loc_el.get_text(strip=True) if loc_el else "Brak lokalizacji"

        # Globalne słowa zakazane
        if FORBIDDEN_RE.search(title):
            save_seen(offer_id, title, price, "skip_forbidden_title")
            seen_ids.add(offer_id)
            continue

        # Zakazane frazy w tytule z konfiguracji danego auta
        forbidden_titles = filters.get("forbidden_titles", [])
        if any(bad in title.lower() for bad in forbidden_titles):
            save_seen(offer_id, title, price, "skip_forbidden_target_title")
            seen_ids.add(offer_id)
            continue

        log.info("Nowa oferta: %s (%s)", title, price)
        result = get_ad_details(link)
        time.sleep(random.uniform(2.0, 4.0))

        if result is None:
            detail_failures += 1
            if detail_failures >= DETAIL_FAIL_LIMIT:
                log.warning("%d nieudane pobrania z rzędu, przerywam skan %s", detail_failures, name)
                report_health(state, name, False, "nie pobiera szczegółów ogłoszeń")
                return
            continue
        detail_failures = 0
        details, description, raw_html = result

        # Globalne sprawdzenie diesla po parametrach
        meta_str = " ".join([
            title,
            str(details.get("Paliwo", "")),
            str(details.get("Rodzaj paliwa", "")),
            str(details.get("fuel_type", "")),
            str(details.get("Wersja", "")),
            str(details.get("version", "")),
        ])
        if FORBIDDEN_RE.search(meta_str):
            save_seen(offer_id, title, price, "skip_diesel_param")
            seen_ids.add(offer_id)
            log.info("Odrzucono (diesel w parametrach: %s)", title)
            continue

        # Filtr pojemności silnika z konfiguracji
        capacity_patterns = filters.get("engine_capacity_patterns", [])
        if capacity_patterns:
            engine_capacity = str(details.get("Poj. silnika", "")).replace(" ", "")
            if engine_capacity and not any(cap in engine_capacity for cap in capacity_patterns):
                save_seen(offer_id, title, price, "skip_capacity_mismatch")
                seen_ids.add(offer_id)
                log.info("Odrzucono pojemność silnika (%s): %s", engine_capacity, title)
                continue

        # Filtr mocy silnika z konfiguracji
        forbidden_power = filters.get("forbidden_power", [])
        if forbidden_power:
            engine_power = str(details.get("Moc silnika", details.get("Moc", "")))
            if any(p in engine_power for p in forbidden_power):
                save_seen(offer_id, title, price, "skip_power_mismatch")
                seen_ids.add(offer_id)
                log.info("Odrzucono moc silnika (%s): %s", engine_power, title)
                continue

        ai_opinion = get_ai_analysis(title, price, location, details, description)
        year, mileage, mileage_num = extract_key_metrics(details, raw_html, description, title, ai_opinion)

        # Maksymalny przebieg z konfiguracji
        if mileage_num and mileage_num > mileage_max:
            save_seen(offer_id, title, price, "skip_high_mileage")
            seen_ids.add(offer_id)
            log.info("Odrzucono (przebieg %d > %d km): %s", mileage_num, mileage_max, title)
            continue

        distance_km = get_distance_from_home(location)
        cepik = extract_cepik_data(details, raw_html, description)
        red_flags = check_red_flags(year, mileage_num, title, description)

        msg = build_message(title, price, location, link, cepik, ai_opinion, year, mileage, distance_km, red_flags)
        if tg_send(msg):
            log.info("Wysłano powiadomienie na Telegram")
            save_seen(offer_id, title, price, "alert_sent")
            seen_ids.add(offer_id)
        else:
            log.error("Nie wysłano alertu, oferta wróci przy następnym skanie: %s", title)


# ============================================================
# MAIN
# ============================================================
def main() -> None:
    lock = acquire_lock()
    if lock is None:
        log.info("Poprzednie uruchomienie jeszcze trwa, wychodzę.")
        return

    seen_ids = load_seen_ids()
    state = load_state()

    # Sprawdzenie czy użytkownik wysłał nowe instrukcje promptu na Telegramie
    check_telegram_commands(state)

    targets = CONFIG.get("targets", [])
    log.info("Uruchomiono scraper. W bazie: %d ogłoszeń. Modeli do przeszukania: %d", len(seen_ids), len(targets))

    try:
        for idx, target in enumerate(targets):
            if idx > 0:
                pause = random.uniform(5.0, 9.0)
                log.info("Czekam %.1fs przed kolejnym modelem...", pause)
                time.sleep(pause)
            scan_target(target, seen_ids, state)
    finally:
        save_state(state)


if __name__ == "__main__":
    main()