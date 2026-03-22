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

# ネットワークドライブだと遅いので /tmp にコピーがあればそちらを優先
_LOCAL_DB = Path("/tmp/toiawasekun.db")
_NETWORK_DB = Path(__file__).resolve().parent.parent / "data" / "toiawasekun.db"
DB_PATH = _LOCAL_DB if _LOCAL_DB.is_file() else _NETWORK_DB
PROMPT_PATH = Path(__file__).resolve().parent.parent / "templates" / "outreach_prompt.txt"
