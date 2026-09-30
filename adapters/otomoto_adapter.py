"""Otomoto — selektory zweryfikowane na prawdziwym zrzucie strony wyników (wrzesień 2026)."""
from __future__ import annotations

from typing import List

from bs4 import BeautifulSoup

from .base import BaseAdapter, Listing, find_price, to_int


class OtomotoAdapter(BaseAdapter):
    platform = "otomoto"
    base_url = "https://www.otomoto.pl"
    wait_selector = "article[data-id]"

    def parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[Listing] = []

        # Karta ogłoszenia: <article data-id> z <h2><a href=".../oferta/...">.
        # Stopka i menu też mają linki, ale nie mają takiej struktury — stąd wcześniejsze
        # śmieci w bazie ("Znajdź nas", "Otomoto" itd.).
        for art in soup.select("article[data-id]"):
            h2 = art.select_one("h2")
            a = h2.select_one("a[href]") if h2 else None
            if not a or "/oferta/" not in a["href"]:
                continue

            params = {
                d["data-parameter"]: d.get_text(strip=True)
                for d in art.select("dd[data-parameter]")
            }

            price_el = art.select_one("h3")
            price = to_int(price_el.get_text()) if price_el else None
            if price is None:
                price = find_price(art.get_text(" ", strip=True))

            blurb_el = h2.find_next("p")
            location = None
            for p in art.select("ul li p"):
                t = p.get_text(strip=True)
                if "(" in t and ")" in t:
                    location = t
                    break

            img = art.select_one("img[src]")
            out.append(Listing(
                platform=self.platform,
                ext_id=art["data-id"],
                title=a.get_text(strip=True) or a.get("aria-label", ""),
                url=a["href"].split("?")[0],
                price=price,
                year=to_int(params.get("year")),
                mileage=to_int(params.get("mileage")),
                fuel=params.get("fuel_type"),
                gearbox=params.get("gearbox"),
                location=location,
                description=blurb_el.get_text(" ", strip=True) if blurb_el else "",
                photo_url=img["src"] if img else None,
            ))
        return out
