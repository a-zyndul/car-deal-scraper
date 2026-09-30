import logging
from typing import Optional

import requests

import config
from adapters.base import Listing

log = logging.getLogger("ai")


class AIAnalyzer:
    """Krótka ocena ogłoszenia: Ollama (lokalnie) z zapasem w Groq. Przy błędzie zwraca None
    — komunikat o błędzie NIE trafia już do alertu na Telegramie."""

    def __init__(self, provider: str = config.AI_PROVIDER):
        self.provider = provider

    def analyze(self, l: Listing, market_hint: str = "") -> Optional[str]:
        if self.provider == "none":
            return None
        prompt = self._prompt(l, market_hint)
        order = ["ollama", "groq"] if self.provider == "ollama" else ["groq", "ollama"]
        for name in order:
            if name == "groq" and not config.GROQ_API_KEY:
                continue
            try:
                out = self._ollama(prompt) if name == "ollama" else self._groq(prompt)
                if out:
                    return out
            except Exception as exc:
                log.warning("AI (%s) nie odpowiedziało: %s", name, exc)
        return None

    @staticmethod
    def _prompt(l: Listing, market_hint: str) -> str:
        facts = [f"Tytuł: {l.title}", f"Cena: {l.price} zł" if l.price else "Cena: brak"]
        if l.year:
            facts.append(f"Rocznik: {l.year}")
        if l.mileage:
            facts.append(f"Przebieg: {l.mileage} km")
        if l.fuel:
            facts.append(f"Paliwo: {l.fuel}")
        facts.append(f"Opis/skrót: {l.description or '(brak opisu)'}")
        if market_hint:
            facts.append(market_hint)
        return (
            "Jesteś doświadczonym rzeczoznawcą samochodowym. Oceń ogłoszenie:\n"
            + "\n".join(facts)
            + "\n\nNapisz po polsku maksymalnie 3 zdania: (1) czy cena pasuje do rocznika i przebiegu "
            "(opieraj się na podanym kontekście rynkowym, nie zmyślaj widełek), (2) czy w opisie są "
            "sygnały ostrzegawcze (uszkodzenia, import z USA, cofnięty licznik, 'do poprawek'). "
            "Jeśli opis jest za krótki, napisz to wprost."
        )

    @staticmethod
    def _ollama(prompt: str) -> Optional[str]:
        r = requests.post(
            config.OLLAMA_URL,
            json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=90,  # zimny start modelu na CPU bywa wolny
        )
        r.raise_for_status()
        return (r.json().get("response") or "").strip() or None

    @staticmethod
    def _groq(prompt: str) -> Optional[str]:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={"model": config.GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 250},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip() or None
