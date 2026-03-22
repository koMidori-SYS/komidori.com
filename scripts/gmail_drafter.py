"""komidori Gmail下書き保存

フォームURLがない店舗にはメールで営業。
Gmail IMAPに下書きを保存し、手動で確認→送信する。
"""
from __future__ import annotations

import imaplib
import logging
from email.mime.text import MIMEText

from scripts.config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD

logger = logging.getLogger("komidori.gmail_drafter")


def save_draft(to: str, subject: str, body: str) -> bool:
    """GmailのDraftsフォルダに下書きを保存"""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        logger.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to
    msg["Subject"] = subject

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

        # Draftsフォルダを検出（日本語環境対応）
        drafts_folder = _find_drafts_folder(imap)
        imap.append(drafts_folder, "\\Draft", None, msg.as_bytes())
        imap.logout()
        logger.info("Draft saved: to=%s subject=%s", to, subject)
        return True
    except Exception as e:
        logger.error("Gmail draft save failed: %s", e)
        return False


def _find_drafts_folder(imap) -> str:
    """GmailのDraftsフォルダ名を検出"""
    _, folders = imap.list()
    for f in (folders or []):
        decoded = f.decode("utf-8", errors="replace") if isinstance(f, bytes) else str(f)
        if "\\Drafts" in decoded:
            # フォルダ名を抽出
            parts = decoded.split('"')
            if len(parts) >= 4:
                return parts[-2]
    return "[Gmail]/Drafts"  # デフォルト
