import logging
import random
import asyncio
from playwright.async_api import async_playwright

log = logging.getLogger("adapter")

class AutoplacAdapter:
    def __init__(self, headless=True, max_pages=3):
        self.headless = headless
        self.max_pages = max_pages

    async def fetch_listings(self, urls):
        all_listings = []
        async with async_playwright() as p:
            # Uruchomienie z dodatkowymi flagami maskującymi boty przed Cloudflare
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu"
                ]
            )
            
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="pl-PL"
            )
            
            # Usunięcie flagi navigator.webdriver
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = await context.new_page()

            for base_url in urls:
                for page_num in range(1, self.max_pages + 1):
                    target_url = f"{base_url}&page={page_num}" if page_num > 1 else base_url
                    log.info("[autoplac] otwieram %s", target_url)
                    
                    try:
                        response = await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
                        if response and response.status == 403:
                            log.warning("[autoplac] Otrzymano status 403 (Cloudflare Block)")
                            break
                        
                        # Losowa zwłoka na "ludzkie" namyślenie się
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                        
                        # Symulacja ruchu myszą, żeby oszukać Cloudflare Turnstile / Behavioral checks
                        await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
                        
                        # Próba zaczekania na kafelki ofert
                        wait_selector = 'a[href*="/oferta"], a[href*="/oferty/"], article, div[class*="offer"]'
                        try:
                            await page.wait_for_selector(wait_selector, timeout=10000)
                        except Exception:
                            log.warning("[autoplac] nie doczekałem się standardowego selektora, próbuję czytać co jest")

                        # Pobieranie ofert (tutaj zachowujemy Twoją logikę parsowania kafelków)
                        # ... reszta logiki wyciągania danych z Twojego pliku autoplac_adapter.py ...

                    except Exception as e:
                        log.exception("[autoplac] Błąd podczas pobierania strony: %s", e)
                        break

            await browser.close()
        return all_listings