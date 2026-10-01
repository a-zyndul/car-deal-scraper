import logging
import random
import time
from typing import Iterable, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from adapters.base import USER_AGENT, BaseAdapter, Listing, generic_cards

log = logging.getLogger("adapter")


class AutoplacAdapter(BaseAdapter):
    platform = "autoplac"
    base_url = "https://autoplac.pl"
    wait_selector = "a[href*='/oferta'], a[href*='/oferty/']"

    def parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        items = generic_cards(soup, r"/ofert[ay]/", self.base_url)
        for it in items:
            if it.photo_url:
                # Jeśli link jest względny (np. /images/...), doklej https://autoplac.pl
                if it.photo_url.startswith("/"):
                    it.photo_url = urljoin(self.base_url, it.photo_url)
                # Odrzuć zaślepki base64 (data:image), pliki .svg i błędne adresy
                if (
                    not it.photo_url.startswith(("http://", "https://"))
                    or ".svg" in it.photo_url.lower()
                    or " " in it.photo_url
                ):
                    it.photo_url = None
        return items

    def fetch_listings(self, urls: Iterable[str]) -> List[Listing]:
        results: List[Listing] = []
        seen: set = set()
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                    "--window-size=1920,1080",
                ],
            )
            ctx = browser.new_context(
                locale="pl-PL",
                timezone_id="Europe/Warsaw",
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT,
                extra_http_headers={
                    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['pl-PL', 'pl', 'en-US', 'en']});
            """)
            page = ctx.new_page()
            try:
                for url in urls:
                    results += self._crawl(page, url, seen)
            finally:
                browser.close()
        return results

    def _load(self, page, url: str) -> str:
        log.info("[%s] otwieram %s", self.platform, url)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        time.sleep(random.uniform(2.5, 4.5))
        for _ in range(3):
            page.mouse.move(random.randint(100, 800), random.randint(100, 600), steps=10)
            time.sleep(random.uniform(0.4, 0.9))

        self._accept_cookies(page)

        try:
            page.wait_for_selector(self.wait_selector, timeout=20_000)
        except Exception:
            log.warning("[%s] nie doczekałem się selektora %r", self.platform, self.wait_selector)

        for _ in range(6):
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(400)

        return page.content()