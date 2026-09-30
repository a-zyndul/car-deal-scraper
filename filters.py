"""Twarde filtrowanie po stronie bota.

Serwisy potrafią po cichu zignorować filtry z URL-a (tak właśnie było z Otomoto:
strona w debug_page.html to wszystkie auta od 2010, nie Fiat Tipo), a do wyników
wstrzykują reklamy i "podobne oferty". Dlatego każdą ofertę sprawdzamy jeszcze raz.
Nieznane pola (np. brak przebiegu na karcie) NIE dyskwalifikują oferty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from adapters.base import Listing


@dataclass
class Criteria:
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    mileage_max: Optional[int] = None
    title_keywords_any: List[str] = field(default_factory=list)   # np. ["tipo", "astra"]
    exclude_fuels: List[str] = field(default_factory=list)        # np. ["diesel", "elektry"]
    exclude_brands: List[str] = field(default_factory=list)       # np. ["kia", "hyundai"]
    exclude_phrases: List[str] = field(default_factory=list)      # w tytule/opisie

    @classmethod
    def from_dict(cls, d: dict) -> "Criteria":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def check(self, l: Listing) -> Tuple[bool, str]:
        title = l.title.lower()
        blob = f"{l.title} {l.description}".lower()

        if l.price is None:
            return False, "brak ceny"
        if self.price_min is not None and l.price < self.price_min:
            return False, "cena za niska"
        if self.price_max is not None and l.price > self.price_max:
            return False, "cena za wysoka"
        if l.year is not None:
            if self.year_min is not None and l.year < self.year_min:
                return False, "za stary rocznik"
            if self.year_max is not None and l.year > self.year_max:
                return False, "za nowy rocznik"
        if l.mileage is not None and self.mileage_max is not None and l.mileage > self.mileage_max:
            return False, "za duży przebieg"
        if self.title_keywords_any and not any(k.lower() in title for k in self.title_keywords_any):
            return False, "inny model"
        if any(b.lower() in title for b in self.exclude_brands):
            return False, "wykluczona marka"
        fuel_blob = f"{l.fuel or ''} {title}".lower()
        if any(f.lower() in fuel_blob for f in self.exclude_fuels):
            return False, "wykluczone paliwo"
        if any(p.lower() in blob for p in self.exclude_phrases):
            return False, "fraza wykluczająca"
        return True, "ok"
