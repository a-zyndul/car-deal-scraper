# 🚗 AI Car Deal Hunter (Hybrid Edge/Cloud Architecture)

Autonomiczny agent monitorujący rynek wtórny samochodów (OLX/Otomoto) w czasie rzeczywistym. Wykorzystuje zaawansowaną analizę rzeczoznawczą opartą na modelach LLM w architekturze hybrydowej (lokalne GPU przez Tailscale z automatycznym fallbackiem na Groq Cloud) oraz powiadomienia na Telegram.

---

## 🏗 Architektura systemu

```
[ OLX / Otomoto ] 
       │ (TLS Fingerprinting / curl_cffi)
       ▼
[ Oracle Cloud VM ] ─── Harmonogram Systemd (co 15 min)
       │
       ├──► [ Tailscale Mesh VPN ] ──► Domowe GPU (Radeon RX 9070 XT + Ollama Qwen 2.5 7B)
       │                                     │ (timeout / offline)
       │                                     ▼
       └─────────────────────────────► [ Groq Cloud API ] (Fallback: OpenAI GPT-OSS / Llama 3.3)
                                             │
                                             ▼
                                    [ Telegram Bot API ]
```

### Kluczowe funkcje:
- **Hybrydowa inferencja (Edge-first):** Serwer w chmurze przesyła dane do domowej stacji roboczej przez prywatną sieć Tailscale Mesh, wykorzystując moc karty graficznej (Ollama). W razie wyłączenia stacji roboczej system natychmiast przełącza się na Groq API bez utraty danych.
- **Data-Driven Configuration (`config.json`):** Całkowite oddzielenie logiki scrapującej od definicji poszukiwanych aut, filtrów silnikowych oraz promptu AI.
- **Interaktywne zarządzanie z Telegrama:** Możliwość podglądu i zmiany promptu systemowego mechanika w locie za pomocą komendy `/prompt`.
- **Rygorystyczna filtracja domenowa:** Odsiewanie niechcianych jednostek (np. silników wolnossących, diesli), limit przebiegu oraz automatyczne obliczanie odległości drogowej od miejsca zamieszkania.
- **Detekcja anomalii i handlarskich pułapek:** Heurystyczna analiza rocznego przebiegu oraz wykrywanie podejrzanego słownictwa ("igła", "niemiec płakał", "perełka") z oznaczeniem czerwoną lampką.
- **Ekstrakcja danych CEPiK:** Wyciąganie numerów VIN, numerów rejestracyjnych oraz daty pierwszej rejestracji bezpośrednio z metadanych Next.js i JSON-LD.

---

## 🚀 Szybki start (Instrukcja wdrożenia)

### 1. Klonowanie i instalacja środowiska

```bash
git clone [https://github.com/twoj-user/ai-car-deal-hunter.git](https://github.com/twoj-user/ai-car-deal-hunter.git)
cd ai-car-deal-hunter

# Utworzenie i aktywacja wirtualnego środowiska
python3 -m venv venv
source venv/bin/activate

# Instalacja zależności
pip install -r requirements.txt
```

---

### 2. Konfiguracja zmiennych środowiskowych (`.env`)

Skopiuj szablon i uzupełnij klucze:
```bash
cp .env.example .env
```

Zawartość `.env`:
```env
# Telegram
TELEGRAM_BOT_TOKEN=twoj_token_bota
TELEGRAM_CHAT_ID=twoje_chat_id

# Inferencja lokalna (Tailscale + Ollama)
USE_OLLAMA=1
OLLAMA_HOST=[http://100.120.167.113:11434](http://100.120.167.113:11434)
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_TIMEOUT=12

# Fallback chmurowy (Groq)
GROQ_API_KEY=twoj_klucz_groq
GROQ_MODEL=openai/gpt-oss-20b
GROQ_FALLBACK_MODEL=llama-3.3-70b-versatile
GROQ_MAX_TOKENS=2000

LOG_LEVEL=INFO
```

---

### 3. Konfiguracja kryteriów poszukiwań (`config.json`)

Wszystkie parametry aut, filtrów i promptu rzeczoznawcy definiuje plik `config.json`. Możesz go dostosować pod dowolny model bez edycji kodu Pythona:

```json
{
  "location": {
    "home_city": "Poznań",
    "home_lat": 52.4064,
    "home_lon": 16.9252
  },
  "ai": {
    "system_prompt": "Jesteś bezwzględnym polskim mechanikiem i rzeczoznawcą aut. Oceniasz konkretną ofertę bez lania wody. Pisz czystą, poprawną polszczyzną. Nigdy nie wypisuj braków danych. Pisz wyłącznie o konkretach z ogłoszenia. Odpowiadaj tylko w podanym formacie."
  },
  "targets": [
    {
      "name": "Opel Astra J Kombi 1.4 Turbo",
      "olx_slug": "astra-j-kombi-1.4-turbo",
      "price_min": 20000,
      "price_max": 35000,
      "year_min": 2012,
      "mileage_max": 200000,
      "fuel_types": ["petrol", "lpg"],
      "filters": {
        "engine_capacity_patterns": ["1364", "1400", "1398", "1.4"],
        "forbidden_titles": ["1.6", "1.7", "2.0", "1.8"],