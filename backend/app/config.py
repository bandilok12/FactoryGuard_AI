"""Central configuration. Values come from environment variables (see .env.example)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_DIR = BASE_DIR.parent                              # repo root


def _load_dotenv():
    """Tiny .env loader so we don't need python-dotenv."""
    for p in (PROJECT_DIR / ".env", BASE_DIR / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

APP_NAME = "FactoryGuard AI"
VERSION = "1.0.0"

# Database: SQLite by default; set DATABASE_URL=postgresql+psycopg2://user:pw@host/db for Postgres
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'factoryguard.db'}")

# Security
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please-32+chars-long-key")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "480"))
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 10

# GenAI provider: gemini | groq | openai | offline
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Sensor simulator
SIMULATOR_AUTOSTART = os.getenv("SIMULATOR_AUTOSTART", "true").lower() == "true"
SIMULATOR_INTERVAL_SEC = float(os.getenv("SIMULATOR_INTERVAL_SEC", "10"))

PLANT_TZ = os.getenv("PLANT_TZ", "Asia/Kolkata")

ML_DIR = BASE_DIR / "app" / "ml" / "artifacts"
MANUALS_DIR = BASE_DIR / "data" / "manuals"
FRONTEND_DIR = PROJECT_DIR / "frontend"
