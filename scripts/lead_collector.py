"""komidori リード収集 — とい合わせ君DB検索

使い方:
  python -m scripts.lead_collector --industry 飲食 --n 20
  python -m scripts.lead_collector --industry 美容 --prefecture 東京都 --n 10
  python -m scripts.lead_collector --list-industries
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
from pathlib import Path

from scripts.config import DB_PATH

# ── DB接続 ─────────────────────────────────────────────

def _connect():
    if not DB_PATH.is_file():
        print(f"ERROR: DB not found at {DB_PATH}")
        print("Run: scp root@85.131.252.231:/opt/ai-os/data/toiawasekun.db ./data/")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def list_industries() -> list[str]:
    """DB内の全業種名を返す"""
    conn = _connect()
    try:
        rows = conn.execute("SELECT DISTINCT name FROM Industry ORDER BY name").fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


def search(
    industry: str,
    n: int = 20,
    prefecture: str | None = None,
    exclude_domains: set[str] | None = None,
) -> list[dict]:
    """業種キーワードで企業を検索し、フォームURL付きをn件返す"""
    conn = _connect()
    exclude_domains = exclude_domains or set()

    try:
        # 完全一致で検索（「飲食店」で「飲食料品卸売業」が混ざらないように）
        params: list = [industry]
        where = "c.isDeleted = 0 AND i.name = ?"
        if prefecture:
            where += " AND p.name LIKE ?"
            params.append(f"%{prefecture}%")

        sql = f"""
            SELECT c.id, c.name, c.domain, c.contactPageUrl as contact_form_url,
                   i.name as industry, p.name as prefecture
            FROM Company c
            JOIN Industry i ON c.industryId = i.id
            JOIN Prefecture p ON c.prefectureId = p.id
            WHERE {where}
            ORDER BY RANDOM()
            LIMIT 500
        """
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        # フィルタ: フォームURL付き + ドメイン除外
        filtered = [
            r for r in rows
            if r["contact_form_url"]
            and r["domain"] not in exclude_domains
        ]

        if len(filtered) > n:
            filtered = random.sample(filtered, n)

        results = []
        for r in filtered[:n]:
            results.append({
                "company_name": r["name"],
                "industry": r["industry"],
                "prefecture": r["prefecture"],
                "domain": r["domain"],
                "website_url": f"https://{r['domain']}",
                "contact_form_url": r["contact_form_url"],
                "status": "NEW",
            })

        print(f"Found {len(results)} leads (pool: {len(rows)}, with form: {len(filtered)})")
        return results

    finally:
        conn.close()


def save_csv(leads: list[dict], output: str = "data/leads.csv") -> None:
    """リードをCSVに保存"""
    path = Path(__file__).resolve().parent.parent / output
    fieldnames = ["company_name", "industry", "prefecture", "domain",
                  "website_url", "contact_form_url", "status",
                  "outreach_subject", "outreach_body"]

    # 既存CSVがあれば既存ドメインを取得
    existing_domains: set[str] = set()
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_domains.add(row.get("domain", ""))

    # 重複除外
    new_leads = [l for l in leads if l["domain"] not in existing_domains]

    mode = "a" if path.is_file() else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if mode == "w":
            writer.writeheader()
        for lead in new_leads:
            writer.writerow(lead)

    print(f"Saved {len(new_leads)} new leads to {path} (skipped {len(leads) - len(new_leads)} duplicates)")


def main():
    parser = argparse.ArgumentParser(description="komidori リード収集")
    parser.add_argument("--industry", type=str, help="業種キーワード（例: 飲食, 美容, クリニック）")
    parser.add_argument("--prefecture", type=str, help="都道府県（例: 東京都）")
    parser.add_argument("--n", type=int, default=20, help="取得件数")
    parser.add_argument("--list-industries", action="store_true", help="DB内の全業種を表示")
    parser.add_argument("--output", type=str, default="data/leads.csv", help="出力CSVパス")
    args = parser.parse_args()

    if args.list_industries:
        for ind in list_industries():
            print(ind)
        return

    if not args.industry:
        parser.error("--industry を指定してください")

    leads = search(args.industry, args.n, args.prefecture)
    if leads:
        save_csv(leads, args.output)
        print("\nSample:")
        for l in leads[:3]:
            print(f"  {l['company_name']} | {l['industry']} | {l['prefecture']} | {l['contact_form_url']}")


if __name__ == "__main__":
    main()
