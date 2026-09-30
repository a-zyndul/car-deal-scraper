"""Wspólna logika adapterów: model ogłoszenia, przeglądarka Playwright, paginacja.

Parsowanie HTML (`parse`) jest oddzielone od pobierania strony (`fetch_listings`),
dzięki czemu selektory można testować offline na zapisanym pliku HTML
(patrz tools/parse_file.py).
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

log = logging.getLogger("adapter")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Listing:
    platform: str
    ext_id: str
    title: str
    url: str
    price: Optional[int] = None          # PLN
    year: Optional[int] = None
    mileage: Optional[int] = None        # km
    fuel: Optional[str] = None
    gearbox: Optional[str] = None
    location: Optional[str] = None
    description: str = ""
    photo_url: Optional[str] = None

    @property
    def uid(self) -> str:
        return f"{self.platform}:{self.ext_id}"


# ---------- pomocnicze parsery tekstu ----------

def to_int(text: Optional[str]) -> Optional[int]:
    """'84 553 km' -> 84553, '29 900,50 zł' -> 29900. Zwraca None gdy brak cyfr."""
    if not text:
        return None
    text = text.split(",")[0]
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


_NUM = r"(?<![\d.,])(\d{1,3}(?:[\s\u00a0\u202f.]\d{3})+|\d+)"


def find_price(text: str) -> Optional[int]:
    m = re.search(_NUM + r"\s*(?:zł|PLN)", text, re.I)
    return to_int(m.group(1)) if m else None


def find_mileage(text: str) -> Optional[int]:
    m = re.search(_NUM + r"\s*km\b", text, re.I)
    return to_int(m.group(1)) if m else None


def find_year(text: str) -> Optional[int]:
    """Rok produkcji: pierwsza samodzielna liczba 198x-203x (bez przyszłych lat)."""
    now = time.localtime().tm_year
    for m in re.finditer(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)", text):
        y = int(m.group(1))
        if y <= now + 1:
            return y
    return None


def stable_id(url: str) -> str:
    m = re.search(r"ID([A-Za-z0-9]+)\.html", url)
    if m:
        return m.group(1)
    return hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:12]


def with_page(url: str, page: int, param: str = "page") -> str:
    if page <= 1:
        return url
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q[param] = str(page)
    return urlunparse(parts._replace(query=urlencode(q)))


def generic_cards(soup: BeautifulSoup, link_re: str, base_url: str) -> List[Listing]:
    """Heurystyka dla serwisów bez znanych selektorów: karta = najwyższy przodek linku
    do oferty, który zawiera dokładnie JEDEN taki link oraz cenę."""
    rx = re.compile(link_re)
    by_url = {}
    for a in soup.select("a[href]"):
        if rx.search(a["href"]):
            by_url.setdefault(urljoin(base_url, a["href"]).split("#")[0], a)

    out: List[Listing] = []
    for url, a in by_url.items():
        card, node = a, a
        for _ in range(7):
            node = node.parent
            if node is None or node.name in ("body", "html"):
                break
            uniq = {urljoin(base_url, x["href"]).split("#")[0]
                    for x in node.select("a[href]") if rx.search(x["href"])}
            if len(uniq) > 1:
                break
            card = node
        text = card.get_text(" ", strip=True)
        price = find_price(text)
        if price is None:
            continue  # to nie jest karta ogłoszenia (menu, baner itd.)
        heading = card.select_one("h1, h2, h3, h4")
        title = (heading or a).get_text(" ", strip=True) or a.get("title", "")
        img = card.select_one("img[src], img[data-src]")
        out.append(Listing(
            platform="", ext_id=stable_id(url), title=title, url=url, price=price,
            year=find_year(text), mileage=find_mileage(text),
            description=text[:400],
            photo_url=(img.get("src") or img.get("data-src")) if img else None,
        ))
    return out


# ---------- bazowy adapter ----------

class BaseAdapter:
    platform = "base"
    base_url = ""
    wait_selector = "body"      # na co czekamy zanim weźmiemy HTML
    page_param = "page"

    def __init__(self, headless: bool = True, max_pages: int = 3, debug_dir: str = "debug"):
        self.headless = headless
        self.max_pages = max_pages
        self.debug_dir = Path(debug_dir)

    def parse(self, html: str) -> List[Listing]:  # do nadpisania
        raise NotImplementedError

    def fetch_listings(self, urls: Iterable[str]) -> List[Listing]:
        from playwright.sync_api import sync_playwright

        results: List[Listing] = []
        seen: set = set()
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                locale="pl-PL", timezone_id="Europe/Warsaw",
                viewport={"width": 1366, "height": 900}, user_agent=USER_AGENT,
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            try:
                for url in urls:
                    results += self._crawl(page, url, seen)
            finally:
                browser.close()
        return results

    def _crawl(self, page, url: str, seen: set) -> List[Listing]:
        out: List[Listing] = []
        for n in range(1, self.max_pages + 1):
            html = self._load(page, with_page(url, n, self.page_param))
            items = self.parse(html)
            for it in items:
                it.platform = self.platform
            log.info("[%s] strona %d: %d ogłoszeń", self.platform, n, len(items))
            if not items:
                if n == 1:
                    self._dump(page, html)
                break
            fresh = [i for i in items if i.uid not in seen]
            if not fresh:
                break  # paginacja zawróciła na te same wyniki
            seen.update(i.uid for i in fresh)
            out += fresh
            time.sleep(random.uniform(1.5, 3.5))
        return out

    def _load(self, page, url: str) -> str:
        log.info("[%s] otwieram %s", self.platform, url)
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        self._accept_cookies(page)
        try:
            page.wait_for_selector(self.wait_selector, timeout=15_000)
        except Exception:
            log.warning("[%s] nie doczekałem się selektora %r", self.platform, self.wait_selector)
        for _ in range(6):  # lazy-load: przewiń stronę stopniowo
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(350)
        return page.content()

    @staticmethod
    def _accept_cookies(page) -> None:
        for sel in ("#onetrust-accept-btn-handler",
                    "button:has-text('Akceptuję')",
                    "button:has-text('Zgadzam się')",
                    "button:has-text('Accept')"):
            try:
                page.click(sel, timeout=2_500)
                page.wait_for_timeout(400)
                return
            except Exception:
                continue

    def _dump(self, page, html: str) -> None:
        """Zero wyników na 1. stronie = zapisz HTML i zrzut ekranu do analizy."""
        self.debug_dir.mkdir(exist_ok=True)
        (self.debug_dir / f"{self.platform}_page.html").write_text(html, encoding="utf-8")
        try:
            page.screenshot(path=str(self.debug_dir / f"{self.platform}_page.png"), full_page=True)
        except Exception:
            pass
        log.warning("[%s] 0 wyników — zapisałem debug/%s_page.html", self.platform, self.platform)
