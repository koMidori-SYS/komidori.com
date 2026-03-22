"""komidori 営業バッチ — ワンコマンドで営業フロー実行

使い方:
  # リスト収集→営業文生成→フォーム送信/Gmail下書き
  python -m scripts.batch --industry 飲食 --n 10

  # 既存CSVから営業文生成+送信のみ
  python -m scripts.batch --csv-only

  # DRY_RUNで全フロー確認（実際の送信はしない）
  DRY_RUN=true python -m scripts.batch --industry 美容 --n 5
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from pathlib import Path

from scripts.config import DRY_RUN

logger = logging.getLogger("komidori.batch")

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "leads.csv"
MAX_SUBMISSIONS = 10
INTERVAL_SEC = 5


def _load_leads() -> list[dict]:
    """CSVからリードを読み込み"""
    if not CSV_PATH.is_file():
        logger.error("leads.csv not found. Run lead_collector first.")
        return []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_leads(leads: list[dict]) -> None:
    """リードをCSVに書き戻し"""
    if not leads:
        return
    fieldnames = list(leads[0].keys())
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leads)


def run_batch(industry: str | None = None, n: int = 10, csv_only: bool = False):
    """営業バッチ実行"""
    # Step 1: リスト収集（csv_onlyでなければ）
    if not csv_only and industry:
        from scripts.lead_collector import search, save_csv
        leads = search(industry, n)
        if leads:
            save_csv(leads)

    # Step 2: CSVから未処理リードを取得
    all_leads = _load_leads()
    new_leads = [l for l in all_leads if l.get("status") == "NEW"]
    logger.info("Total leads: %d, NEW: %d", len(all_leads), len(new_leads))

    if not new_leads:
        print("No NEW leads to process.")
        return

    # Step 3: 営業文生成 + 送信
    from scripts.outreach_generator import generate
    from scripts.form_submitter import submit_form, SenderInfo

    sender = SenderInfo(
        name="千葉 実佑",
        company="koMidori",
        email="info@komidori.com",
        phone="",
        furigana="チバ ミユ",
    )

    processed = 0
    for lead in new_leads[:MAX_SUBMISSIONS]:
        company = lead["company_name"]
        industry_name = lead.get("industry", "")
        prefecture = lead.get("prefecture", "")
        form_url = lead.get("contact_form_url", "")

        # 営業文生成
        print(f"\n--- {company} ({industry_name}) ---")
        email_content = generate(company, industry_name, prefecture)
        subject = email_content.get("subject", "")
        body = email_content.get("body", "")

        if not subject or not body:
            print(f"  SKIP: 営業文生成失敗")
            continue

        print(f"  Subject: {subject}")
        print(f"  Body: {body[:100]}...")

        # CSVに営業文を記録
        lead["outreach_subject"] = subject
        lead["outreach_body"] = body

        # フォームURL があればフォーム送信（DRY_RUNでもフィールド確認）
        if form_url:
            result = submit_form(
                contact_url=form_url,
                sender=sender,
                subject=subject,
                body=body,
                dry_run=DRY_RUN,
            )
            if result.get("success"):
                status = "DRY_RUN" if DRY_RUN else "FORM_SENT"
                lead["status"] = status
                print(f"  {status}: {form_url}")
            else:
                lead["status"] = "FORM_FAILED"
                print(f"  FORM_FAILED: {result.get('reason', 'unknown')}")

                # フォーム失敗 → Gmail下書きにフォールバック
                email = lead.get("email", "")
                if email:
                    from scripts.gmail_drafter import save_draft
                    ok = save_draft(email, subject, body)
                    if ok:
                        lead["status"] = "DRAFT_SAVED"
                        print(f"  DRAFT_SAVED: {email}")
        else:
            # フォームなし → メールアドレスがあればGmail下書き
            email = lead.get("email", "")
            if email:
                from scripts.gmail_drafter import save_draft
                ok = save_draft(email, subject, body)
                if ok:
                    lead["status"] = "DRAFT_SAVED"
                    print(f"  DRAFT_SAVED: {email}")
            else:
                lead["status"] = "NO_CONTACT"
                print(f"  NO_CONTACT: フォームもメールもなし")

        processed += 1
        if processed < MAX_SUBMISSIONS:
            time.sleep(INTERVAL_SEC)

    # Step 4: CSV更新
    _save_leads(all_leads)
    print(f"\nDone: {processed} leads processed. CSV updated.")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="komidori 営業バッチ")
    parser.add_argument("--industry", type=str, help="業種キーワード")
    parser.add_argument("--n", type=int, default=10, help="リスト収集件数")
    parser.add_argument("--csv-only", action="store_true", help="既存CSVからのみ処理")
    args = parser.parse_args()

    if not args.csv_only and not args.industry:
        parser.error("--industry か --csv-only を指定してください")

    if DRY_RUN:
        print("*** DRY_RUN MODE — 実際の送信はしません ***\n")

    run_batch(args.industry, args.n, args.csv_only)


if __name__ == "__main__":
    main()
