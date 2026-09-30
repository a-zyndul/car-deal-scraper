from __future__ import annotations

from typing import List

from bs4 import BeautifulSoup

from .base import BaseAdapter, Listing, generic_cards

OFFER_LINK_RE = r"/(oferta|oferty|ogloszenie|samochod)[/-][^?#]*\d"


class AutoplacAdapter(BaseAdapter):
    platform = "autoplac"
    base_url = "https://autoplac.pl"
    wait_selector = "a[href*='/oferta'], a[href*='/oferty/']"

    def parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        items = generic_cards(soup, OFFER_LINK_RE, self.base_url)
        for it in items:
            it.platform = self.platform
        return items
