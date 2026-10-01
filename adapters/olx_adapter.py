"""OLX (Motoryzacja). Oparty na znanej strukturze kart OLX (data-cy="l-card"),
ale NIE zweryfikowany na żywej stronie — patrz README, sekcja "Strojenie adapterów"."""
from __future__ import annotations

from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import (BaseAdapter, Listing, find_mileage, find_price, find_year,
                   stable_id, to_int)


class OlxAdapter(BaseAdapter):
    platform = "olx"
    base_url = "https://www.olx.pl"
    wait_selector = 'div[data-cy="l-card"], div[data-testid="listing-grid"], article'

    def parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[Listing] = []

        for card in soup.select('div[data-cy="l-card"]'):
            a = card.select_one("a[href]")
            if not a:
                continue
            url = urljoin(self.base_url, a["href"]).split("?")[0]

            title_el = card.select_one("h4, h6, h3")
            title = (title_el or a).get_text(" ", strip=True)

            price_el = card.select_one('[data-testid="ad-price"]')
            price_txt = price_el.get_text(" ", strip=True) if price_el else ""
            price = find_price(price_txt) or to_int(price_txt) or find_price(card.get_text(" ", strip=True))

            # blok "lokalizacja - data" wycinamy, żeby "Odświeżono 29 września 2026"
            # nie wyszło jako rocznik auta
            loc_el = card.select_one('[data-testid="location-date"]')
            location = loc_el.get_text(" ", strip=True) if loc_el else None
            if loc_el:
                loc_el.extract()
            rest = card.get_text(" ", strip=True).replace(title, " ")

            img = card.select_one("img[src]")
            out.append(Listing(
                platform=self.platform,
                ext_id=card.get("id") or stable_id(url),
                title=title,
                url=url,
                price=price,
                year=find_year(rest),
                mileage=find_mileage(rest),
                location=location,
                description=title,
                photo_url=img["src"] if img and img["src"].startswith("http") else None,
            ))
        return out
