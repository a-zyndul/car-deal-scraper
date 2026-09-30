import html
import logging
import time
from typing import Optional

import requests

from adapters.base import Listing
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("notifier")

SOURCE_LABEL = {"otomoto": "Otomoto", "olx": "OLX", "autoplac": "Autoplac"}


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def format_message(l: Listing, ai_opinion: Optional[str] = None) -> str:
    """HTML zamiast Markdown: w tytułach/opisach często są znaki _ * [ ], które
    wywalały Telegramowy parse_mode=Markdown błędem 400."""
    e = html.escape
    lines = [f"🚗 <b>{e(l.title)}</b>"]
    lines.append(f"💰 <b>{_n(l.price)} zł</b>" if l.price is not None else "💰 cena: brak w ogłoszeniu")

    specs = []
    if l.year:
        specs.append(f"📅 {l.year}")
    if l.mileage:
        specs.append(f"🛣 {_n(l.mileage)} km")
    if l.fuel:
        specs.append(f"⛽ {e(l.fuel)}")
    if l.gearbox:
        specs.append(f"⚙️ {e(l.gearbox)}")
    if specs:
        lines.append("  ".join(specs))
    if l.location:
        lines.append(f"📍 {e(l.location)}")
    if ai_opinion:
        lines.append(f"\n🤖 {e(ai_opinion[:600])}")
    label = SOURCE_LABEL.get(l.platform, l.platform)
    lines.append(f'\n🔗 <a href="{e(l.url, quote=True)}">Otwórz na {label}</a>')
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: Optional[str] = TELEGRAM_BOT_TOKEN, chat_id: Optional[str] = TELEGRAM_CHAT_ID):
        self.token, self.chat_id = token, chat_id
        self.base_url = f"https://api.telegram.org/bot{token}" if token else None

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, l: Listing, ai_opinion: Optional[str] = None) -> bool:
        if not self.configured:
            log.warning("Brak konfiguracji Telegrama w .env — pomijam alert.")
            return False
        text = format_message(l, ai_opinion)

        if l.photo_url and len(text) <= 1000:  # limit podpisu zdjęcia = 1024
            if self._post("sendPhoto", {"photo": l.photo_url, "caption": text}):
                return True
            # Telegram nie zawsze potrafi pobrać zdjęcie z CDN — wtedy zwykła wiadomość
        return self._post("sendMessage", {"text": text, "disable_web_page_preview": False})

    def _post(self, method: str, payload: dict, retry: bool = True) -> bool:
        payload = {"chat_id": self.chat_id, "parse_mode": "HTML", **payload}
        try:
            r = requests.post(f"{self.base_url}/{method}", json=payload, timeout=15)
        except requests.RequestException as exc:
            log.error("Telegram %s: %s", method, exc)
            return False
        if r.status_code == 200:
            return True
        if r.status_code == 429 and retry:
            wait = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(min(wait, 30) + 1)
            return self._post(method, payload, retry=False)
        log.error("Telegram %s -> %s: %s", method, r.status_code, r.text[:300])
        return False
