"""komidori 設定・環境変数管理"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "info@komidori.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "toiawasekun.db"
PROMPT_PATH = Path(__file__).resolve().parent.parent / "templates" / "outreach_prompt.txt"
