# 🚗 AI Car Deal Hunter

Osobisty agent, który co ~15 minut sprawdza nowe ogłoszenia samochodów na OLX, odsiewa handlarzy i niechciane silniki, ocenia ofertę modelem językowym i wysyła alert na Telegram. Działa jako usługa `systemd` na darmowej maszynie Oracle Cloud (Always Free).

> Projekt do użytku osobistego: kilka zapytań na kwadrans, z losowymi opóźnieniami. Przed uruchomieniem sprawdź regulamin serwisu i `robots.txt`.

## Architektura

```
[ OLX ] ── curl_cffi ──► [ Oracle Cloud VM (Ubuntu, systemd timer co ~15 min) ]
                                  │
                                  ├─► Ollama na domowym GPU (przez Tailscale)
                                  │        │ offline / timeout / zły format
                                  │        ▼
                                  └─► Groq API (gpt-oss-20b → llama-3.3-70b)
                                           │
                                           ▼
                                   Telegram Bot API
```

## Co faktycznie robi

- **Skan i deduplikacja:** lista wyników (`__NEXT_DATA__` lub karty HTML), baza widzianych ogłoszeń w `seen_cars.jsonl`, pierwsze uruchomienie tylko indeksuje oferty (bez zalewu alertów).
- **Filtry sterowane konfiguracją (`config.json`):** zakazane słowa w tytule, zakazane paliwa, opcjonalny limit przebiegu, wykrywanie handlarzy (konta firmowe i frazy typu „faktura VAT", „raty").
- **Dane do CEPiK:** VIN, numer rejestracyjny i data pierwszej rejestracji wyciągane z parametrów i opisu (tylko przy jednoznacznej etykiecie, żeby nie zgadywać).
- **Odległość w linii prostej** od miejsca zamieszkania, gdy ogłoszenie ma współrzędne.
- **Analiza AI z hybrydową inferencją:** lokalny model przez Tailscale, a przy awarii Groq.
- **Odporność:** blokada przed równoległym uruchomieniem, ostrzeżenie na Telegramie po kilku nieudanych skanach z rzędu, ponawianie ofert, których nie udało się pobrać.

## Jak ograniczam „halucynacje" AI

1. **Wymuszony JSON** (schemat w Ollamie, `json_object` w Groq) zamiast swobodnego tekstu, a wiadomość składa kod, nie model.
2. **Walidacja:** ocena musi być liczbą 1–10, teksty mają limity długości, puste odpowiedzi i „brak danych" są wycinane.
3. **Sprawdzanie zgodności z opisem:** wzmianki o serwisie i LPG są usuwane, jeśli sprzedawca ich nie napisał.
4. **Opis ogłoszenia to dane, nie polecenia** (ochrona przed prompt injection z treści ogłoszenia).
5. Niska temperatura, stały seed, pola bez danych w ogóle nie trafiają do promptu.
6. Gdy model zwróci błąd lub zły format, kod przechodzi do kolejnego silnika. Alert wychodzi nawet bez AI.

## Wdrożenie (Oracle Cloud, Ubuntu 24.04)

```bash
git clone https://github.com/<twoj-user>/ai-car-deal-hunter.git ~/scraper
cd ~/scraper
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env && chmod 600 .env   # uzupełnij klucze
```

Na serwerze ustaw w `.env` `USE_OLLAMA=0`, jeśli nie masz dostępu do domowego GPU. Adres Ollamy (`OLLAMA_HOST`) to adres Tailscale komputera z GPU.

```bash
sudo cp car-scraper.service car-scraper.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now car-scraper.timer

systemctl list-timers | grep car-scraper
journalctl -u car-scraper -n 50 --no-pager
```

Pliki jednostek muszą mieć końcówki linii LF. Jeśli edytowałeś je na Windowsie: `sed -i 's/\r$//' car-scraper.*`. Repozytorium wymusza LF w `.gitattributes`.

## Konfiguracja

- `.env`: tokeny, adresy i modele (wzór w `.env.example`).
- `config.json`: `search_targets` (adres wyszukiwania z OLX, opcjonalnie `title_keywords`), `filters`, `dealer_exclusions`, `ai.persona`, `ai.known_issues`. Filtry paliwa i typu nadwozia najłatwiej ustawić na OLX i wkleić gotowy adres do konfiguracji.

## Testy

```bash
./venv/bin/pip install pytest
./venv/bin/pytest
```

## Ograniczenia i pomysły

- Selektory HTML OLX mogą się zmienić. Wtedy scraper wyśle ostrzeżenie o braku wyników.
- Do zrobienia: parser ogłoszeń Otomoto, sterowanie promptem komendą z Telegrama, heurystyka nietypowego przebiegu rocznego.
