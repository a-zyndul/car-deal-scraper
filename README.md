# 🚗 Car Deal Scraper

Bot skanuje **Otomoto, OLX i Autoplac**, rygorystycznie filtruje oferty według Twoich kryteriów (cena, rocznik, przebieg, słowa wykluczające) i natychmiast wysyła nowe powiadomienia na Telegram (wraz ze zdjęciem i linkiem). Zaprojektowany do stabilnej pracy 24/7 na serwerze (np. Oracle Cloud).

## Start
```bash
pip install -r requirements.txt
playwright install chromium
sudo playwright install-deps       # wymagane na serwerach bez interfejsu graficznego (Linux)

cp .env.example .env               # podaj token i chat id Telegrama (zostaw AI_PROVIDER=none)
cp config.example.json config.json # wklej URL-e wyszukiwań + dopasuj twarde kryteria

python main.py --dry-run           # pokaż co by poszło (weryfikacja w konsoli)
python main.py --seed              # zapamiętaj obecne oferty z portali (bez spamu na Telegram)
python main.py --loop              # uruchom w trybie ciągłym (odpytywanie co CHECK_INTERVAL_MINUTES)