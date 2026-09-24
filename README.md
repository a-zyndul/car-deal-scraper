# 🚗 AI Car Deal Scraper (Hybrid Edge/Cloud Architecture)

Zautomatyzowany agent do monitorowania rynku wtórnego samochodów (OLX/Otomoto) z hybrydową architekturą wnioskowania LLM i alertami na Telegram w czasie rzeczywistym.

## 🏗 Architektura systemu

[ OLX / Otomoto ]
│ (TLS Fingerprinting / curl_cffi)
▼
[ Oracle Cloud VM ] ─── Cron / Systemd Timer
│
├──► [ Tailscale Mesh VPN ] ──► Domowe GPU (Radeon RX 9070 XT + Ollama Qwen 2.5 7B)
│                                     │ (jeśli PC wyłączony / timeout)
│                                     ▼
└─────────────────────────────► [ Groq Cloud API ] (Fallback: GPT-OSS / Llama 3.3)
│
▼
[ Telegram Bot API ]

### Kluczowe funkcje:
- **Hybrydowy pipeline AI (Edge-first):** Serwer w chmurze przesyła dane do domowej stacji roboczej przez prywatną sieć Tailscale, wykorzystując lokalną moc obliczeniową (Ollama). W przypadku braku łączności następuje bezprzerwowe przełączenie na Groq Cloud.
- **Rygorystyczna filtracja domenowa:** Eliminacja silników Diesla, odrzucanie jednostek wolnossących na rzecz 1.4 Turbo, filtrowanie limitu przebiegu oraz kalkulacja odległości drogowej z wykorzystaniem OpenStreetMap Nominatim.
- **Detekcja anomalii handlarskich:** Analiza heurystyczna wykrywająca podejrzanie niski roczny przebieg oraz słownictwo manipulacyjne ("igła", "niemiec płakał").
- **Ekstrakcja metadanych:** Pobieranie numerów VIN, dat pierwszej rejestracji i tablic bezpośrednio ze struktur `__NEXT_DATA__` i JSON-LD pod kątem weryfikacji w CEPiK.
- **Niezawodność:** Wdrożenie jako usługa `systemd` z systemowym timerem, blokadami procesów (`fcntl.flock`) oraz automatycznym alertowaniem o potencjalnych blokadach IP.

## 🛠 Technologie
- **Backend:** Python 3.10, BeautifulSoup4, curl_cffi, Pydantic
- **Inference:** Ollama (Qwen 2.5), Groq Cloud API
- **Infrastruktura:** Oracle Cloud Infrastructure (Ubuntu VM), Tailscale, Systemd