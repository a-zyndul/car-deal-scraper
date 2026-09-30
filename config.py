import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "true").lower() == "true"
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))
DB_PATH = os.getenv("DB_PATH", "cars.db")

# AI: none | ollama | groq   (przy błędzie ollamy bot próbuje Groq, jeśli jest klucz)
AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def load_search_config(path: str = "config.json") -> dict:
    """config.json trzyma Twoje preferencje (wyszukiwania + kryteria) i jest w .gitignore.
    Wzór: config.example.json."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Brak {path}. Skopiuj config.example.json -> config.json i dostosuj."
        )
    return json.loads(p.read_text(encoding="utf-8"))
