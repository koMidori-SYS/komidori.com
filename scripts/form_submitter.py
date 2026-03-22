"""A-1 問い合わせフォーム自動送信

とい合わせ君と同様に、企業サイトのお問い合わせフォームを
自動解析・自動入力・自動送信する。

フロー:
  1. contact_form_url のHTMLを取得
  2. <form> を解析しフィールドを検出
  3. フィールド名からカテゴリを推定（名前/会社名/メール/電話/件名/本文）
  4. 営業メール内容をフィールドにマッピング
  5. POST送信

制約:
  - JavaScript必須のSPAフォームは対応外（将来Playwright追加可能）
  - CAPTCHA付きフォームはスキップ
  - reCAPTCHA/hCaptcha検出時はスキップ
  - レート制限: 呼び出し元で管理
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import logging

logger = logging.getLogger("komidori.form_submitter")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_TIMEOUT = 15


# ── フォームフィールド分類 ──────────────────────────────────────

class FieldCategory:
    NAME = "name"           # お名前・氏名
    COMPANY = "company"     # 会社名・組織名
    EMAIL = "email"         # メールアドレス
    PHONE = "phone"         # 電話番号
    SUBJECT = "subject"     # 件名
    MESSAGE = "message"     # お問い合わせ内容・本文
    FURIGANA = "furigana"   # フリガナ
    DEPARTMENT = "department"  # 部署
    POSITION = "position"   # 役職
    POSTAL_CODE = "postal_code"  # 郵便番号
    PREFECTURE = "prefecture"    # 都道府県
    ADDRESS = "address"     # 住所（市区町村・番地）
    URL = "url"             # WebサイトURL
    UNKNOWN = "unknown"     # 不明


# フィールド名/ラベル → カテゴリのマッピングルール（優先度順）
_FIELD_RULES: list[tuple[str, list[str]]] = [
    (FieldCategory.EMAIL, [
        "email", "mail", "メール", "e-mail", "eメール",
    ]),
    (FieldCategory.PHONE, [
        "tel", "phone", "電話", "携帯", "fax",
    ]),
    (FieldCategory.FURIGANA, [
        "furigana", "kana", "フリガナ", "ふりがな", "カナ",
    ]),
    (FieldCategory.POSTAL_CODE, [
        "zip", "postal", "郵便番号", "post_code", "postcode", "zipcode",
    ]),
    (FieldCategory.PREFECTURE, [
        "prefecture", "都道府県", "pref",
    ]),
    (FieldCategory.COMPANY, [
        "company", "会社", "組織", "法人", "御社", "貴社", "団体",
    ]),
    (FieldCategory.DEPARTMENT, [
        "department", "部署", "所属", "事業部",
    ]),
    (FieldCategory.POSITION, [
        "position", "役職", "肩書",
    ]),
    (FieldCategory.SUBJECT, [
        "subject", "件名", "タイトル", "title", "用件",
    ]),
    (FieldCategory.MESSAGE, [
        "message", "body", "content", "inquiry", "内容", "本文",
        "問い合わせ", "お問い合わせ", "ご質問", "ご要望", "詳細",
        "相談", "comment", "コメント", "備考", "メッセージ",
    ]),
    (FieldCategory.NAME, [
        "name", "氏名", "名前", "お名前", "担当者",
    ]),
    (FieldCategory.ADDRESS, [
        "address", "住所", "所在地", "市区町村", "番地",
    ]),
    (FieldCategory.URL, [
        "url", "website", "homepage", "ホームページ", "サイト",
    ]),
]


@dataclass
class FormField:
    """フォームのinput/textarea/selectフィールド"""
    tag: str  # input, textarea, select
    name: str  # name属性
    type: str  # type属性（input用）
    required: bool
    placeholder: str
    label: str  # 関連するlabelテキスト
    category: str = FieldCategory.UNKNOWN
    value: str = ""  # 送信時の値
    options: list[str] = field(default_factory=list)  # select用


@dataclass
class ParsedForm:
    """解析済みフォーム"""
    action: str  # 送信先URL
    method: str  # GET or POST
    fields: list[FormField]
    hidden_fields: dict[str, str]  # hiddenフィールド
    has_captcha: bool = False
    has_file_upload: bool = False


class FormSubmitError(Exception):
    """フォーム送信エラー"""
    pass


# ── HTML取得 ────────────────────────────────────────────────────

def _fetch_html(url: str) -> Optional[str]:
    """URLからHTMLを取得"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        })
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as e:
        logger.warning("HTML取得失敗 [%s]: %s", url, e)
        return None


# ── フォーム解析 ────────────────────────────────────────────────

def _classify_field(name: str, placeholder: str, label: str, field_type: str) -> str:
    """フィールド名・placeholder・labelからカテゴリを推定"""
    # type属性から明確な場合
    if field_type == "email":
        return FieldCategory.EMAIL
    if field_type == "tel":
        return FieldCategory.PHONE
    if field_type == "url":
        return FieldCategory.URL

    # name/placeholder/labelを結合して検索
    text = f"{name} {placeholder} {label}".lower()

    for category, keywords in _FIELD_RULES:
        for kw in keywords:
            if kw.lower() in text:
                return category

    return FieldCategory.UNKNOWN


def _extract_labels(html: str) -> dict[str, str]:
    """<label for="xxx">テキスト</label> の対応を抽出"""
    labels = {}
    for m in re.finditer(
        r'<label[^>]*\bfor=["\']([^"\']+)["\'][^>]*>(.*?)</label>',
        html, re.I | re.S,
    ):
        field_id = m.group(1)
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if text:
            labels[field_id] = text
    return labels


def _detect_captcha(html: str) -> bool:
    """CAPTCHA存在チェック"""
    captcha_patterns = [
        r"g-recaptcha", r"h-captcha", r"recaptcha",
        r"captcha", r"hcaptcha", r"turnstile",
        r"data-sitekey", r"grecaptcha",
    ]
    html_lower = html.lower()
    return any(p in html_lower for p in captcha_patterns)


def parse_form(html: str, page_url: str) -> Optional[ParsedForm]:
    """HTMLからお問い合わせフォームを解析。

    複数フォームがある場合、textareaを含むフォーム（＝問い合わせフォーム）を優先。
    """
    # すべての<form>を抽出
    form_pattern = re.compile(
        r'<form([^>]*)>(.*?)</form>', re.I | re.S
    )
    forms = form_pattern.findall(html)
    if not forms:
        logger.debug("フォームが見つかりません: %s", page_url)
        return None

    labels = _extract_labels(html)

    best_form: Optional[ParsedForm] = None
    best_score = -1

    for attrs_str, form_html in forms:
        # action/methodを抽出
        action_m = re.search(r'action=["\']([^"\']*)["\']', attrs_str, re.I)
        method_m = re.search(r'method=["\']([^"\']*)["\']', attrs_str, re.I)

        raw_action = action_m.group(1) if action_m else ""
        method = (method_m.group(1) if method_m else "POST").upper()

        # actionを絶対URLに変換
        if not raw_action or raw_action == "#":
            action = page_url
        elif raw_action.startswith("http"):
            action = raw_action
        elif raw_action.startswith("/"):
            parsed = urllib.parse.urlparse(page_url)
            action = f"{parsed.scheme}://{parsed.netloc}{raw_action}"
        else:
            base = page_url.rsplit("/", 1)[0]
            action = f"{base}/{raw_action}"

        fields: list[FormField] = []
        hidden: dict[str, str] = {}
        has_file = False

        # input フィールド
        for m in re.finditer(
            r'<input([^>]*)/?>', form_html, re.I
        ):
            attr_str = m.group(1)
            name = _attr(attr_str, "name")
            if not name:
                continue
            ftype = _attr(attr_str, "type") or "text"

            if ftype == "hidden":
                hidden[name] = _attr(attr_str, "value")
                continue
            if ftype in ("submit", "button", "image", "reset"):
                continue
            if ftype == "file":
                has_file = True
                continue
            if ftype in ("checkbox", "radio"):
                # チェックボックス/ラジオは個別対応が難しいのでスキップ
                # ただしhidden値とセットになっている場合がある
                continue

            placeholder = _attr(attr_str, "placeholder")
            field_id = _attr(attr_str, "id")
            label = labels.get(field_id, "")
            required = "required" in attr_str.lower()

            category = _classify_field(name, placeholder, label, ftype)
            fields.append(FormField(
                tag="input", name=name, type=ftype,
                required=required, placeholder=placeholder,
                label=label, category=category,
            ))

        # textarea フィールド
        for m in re.finditer(
            r'<textarea([^>]*)>(.*?)</textarea>', form_html, re.I | re.S
        ):
            attr_str = m.group(1)
            name = _attr(attr_str, "name")
            if not name:
                continue
            placeholder = _attr(attr_str, "placeholder")
            field_id = _attr(attr_str, "id")
            label = labels.get(field_id, "")
            required = "required" in attr_str.lower()

            category = _classify_field(name, placeholder, label, "textarea")
            if category == FieldCategory.UNKNOWN:
                category = FieldCategory.MESSAGE  # textareaはデフォルトでメッセージ
            fields.append(FormField(
                tag="textarea", name=name, type="textarea",
                required=required, placeholder=placeholder,
                label=label, category=category,
            ))

        # select フィールド（用件種別などに使われる）
        for m in re.finditer(
            r'<select([^>]*)>(.*?)</select>', form_html, re.I | re.S
        ):
            attr_str = m.group(1)
            name = _attr(attr_str, "name")
            if not name:
                continue
            field_id = _attr(attr_str, "id")
            label = labels.get(field_id, "")
            required = "required" in attr_str.lower()
            options = re.findall(
                r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>([^<]*)',
                m.group(2), re.I,
            )
            option_values = [v for v, t in options if v]
            category = _classify_field(name, "", label, "select")
            fields.append(FormField(
                tag="select", name=name, type="select",
                required=required, placeholder="",
                label=label, category=category,
                options=option_values,
            ))

        # スコアリング: textarea含む（問い合わせフォーム）ほど優先
        score = 0
        if any(f.category == FieldCategory.MESSAGE for f in fields):
            score += 10
        if any(f.category == FieldCategory.EMAIL for f in fields):
            score += 5
        if any(f.category == FieldCategory.NAME for f in fields):
            score += 3
        score += len(fields)

        if score > best_score:
            best_score = score
            best_form = ParsedForm(
                action=action,
                method=method,
                fields=fields,
                hidden_fields=hidden,
                has_captcha=_detect_captcha(form_html),
                has_file_upload=has_file,
            )

    return best_form


def _attr(attr_str: str, name: str) -> str:
    """HTMLタグの属性値を取得"""
    m = re.search(rf'{name}=["\']([^"\']*)["\']', attr_str, re.I)
    return m.group(1) if m else ""


# ── フィールドマッピング ────────────────────────────────────────

@dataclass
class SenderInfo:
    """送信者情報"""
    name: str = "AI経営OS リサーチチーム"
    company: str = "株式会社Caline"
    email: str = ""
    phone: str = ""
    department: str = ""
    position: str = ""
    furigana: str = "エーアイケイエイオーエス"
    postal_code: str = ""     # 郵便番号
    prefecture: str = ""      # 都道府県
    address: str = ""         # 市区町村・番地
    website_url: str = ""     # WebサイトURL


def map_fields(
    form: ParsedForm,
    sender: SenderInfo,
    subject: str,
    body: str,
) -> dict[str, str]:
    """フォームフィールドに送信データをマッピング。

    Returns: {field_name: value} の辞書
    """
    data: dict[str, str] = {}

    # hiddenフィールドはそのまま
    data.update(form.hidden_fields)

    for f in form.fields:
        if f.category == FieldCategory.NAME:
            data[f.name] = sender.name
        elif f.category == FieldCategory.COMPANY:
            data[f.name] = sender.company
        elif f.category == FieldCategory.EMAIL:
            data[f.name] = sender.email
        elif f.category == FieldCategory.PHONE:
            data[f.name] = sender.phone or "03-0000-0000"
        elif f.category == FieldCategory.SUBJECT:
            data[f.name] = subject
        elif f.category == FieldCategory.MESSAGE:
            data[f.name] = body
        elif f.category == FieldCategory.FURIGANA:
            data[f.name] = sender.furigana or "エーアイケイエイオーエス"
        elif f.category == FieldCategory.DEPARTMENT:
            data[f.name] = sender.department or ""
        elif f.category == FieldCategory.POSITION:
            data[f.name] = sender.position or ""
        elif f.category == FieldCategory.POSTAL_CODE:
            data[f.name] = sender.postal_code or ""
        elif f.category == FieldCategory.PREFECTURE:
            data[f.name] = sender.prefecture or ""
        elif f.category == FieldCategory.ADDRESS:
            data[f.name] = sender.address or ""
        elif f.category == FieldCategory.URL:
            data[f.name] = sender.website_url or ""
        elif f.tag == "select" and f.options:
            # selectはデフォルトで最初のoption or 「その他」系
            other = next(
                (o for o in f.options if "その他" in o or "other" in o.lower()),
                None,
            )
            data[f.name] = other or f.options[0]
        elif f.category == FieldCategory.UNKNOWN:
            if f.required:
                # 必須で分類不明 → 名前がそれっぽければ名前
                data[f.name] = sender.name
            # 任意で不明はスキップ

    return data


# ── フォーム送信 ────────────────────────────────────────────────

def submit_form(
    contact_url: str,
    sender: SenderInfo,
    subject: str,
    body: str,
    dry_run: bool = False,
) -> dict:
    """お問い合わせフォームを自動送信する。

    Args:
        contact_url: お問い合わせページURL
        sender: 送信者情報
        subject: 件名
        body: 本文

    Returns:
        {"success": bool, "reason": str, "status_code": int|None}
    """
    result = {"success": False, "reason": "", "status_code": None}

    if not contact_url:
        result["reason"] = "contact_url未設定"
        return result

    # 1. HTML取得
    html = _fetch_html(contact_url)
    if not html:
        result["reason"] = "HTML取得失敗"
        return result

    # 2. フォーム解析
    form = parse_form(html, contact_url)
    if not form:
        result["reason"] = "フォーム未検出"
        return result

    # 3. CAPTCHA検出
    if form.has_captcha:
        result["reason"] = "CAPTCHA検出（スキップ）"
        return result

    # 4. ファイルアップロード検出
    if form.has_file_upload:
        result["reason"] = "ファイルアップロード必須（スキップ）"
        return result

    # 5. メッセージフィールド必須
    has_message = any(f.category == FieldCategory.MESSAGE for f in form.fields)
    if not has_message:
        result["reason"] = "メッセージフィールド未検出"
        return result

    # 6. メールフィールドがある場合、送信者メール必須
    has_email_field = any(f.category == FieldCategory.EMAIL for f in form.fields)
    if has_email_field and not sender.email:
        result["reason"] = "送信者メール未設定"
        return result

    # 7. フィールドマッピング
    data = map_fields(form, sender, subject, body)

    logger.info(
        "フォーム送信準備: %s | fields=%d | action=%s",
        contact_url, len(data), form.action,
    )

    if dry_run:
        result["success"] = True
        result["reason"] = "DRY_RUN"
        logger.info("[DRY_RUN] フォーム送信スキップ: %s", contact_url)
        return result

    # 8. POST送信
    try:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            form.action,
            data=encoded,
            headers={
                "User-Agent": _UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
                "Referer": contact_url,
                "Origin": urllib.parse.urljoin(contact_url, "/"),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            result["status_code"] = resp.status
            resp_html = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8",
                errors="replace",
            )

        # 成功判定: ステータス200〜399 + エラーメッセージがない
        if 200 <= (result["status_code"] or 0) < 400:
            # レスポンスにエラーっぽい文言がないか確認
            error_patterns = [
                "エラー", "error", "入力してください", "必須項目", "正しく",
                "入力が正しくありません", "もう一度", "再入力", "invalid",
                "required", "validation", "failed",
            ]
            success_patterns = [
                "ありがとう", "送信完了", "受け付けました", "thank", "complete",
                "送信しました", "お問い合わせを受け付け", "確認メール",
                "受信しました", "submitted", "received", "success",
            ]
            resp_lower = resp_html.lower()

            has_success = any(p in resp_lower for p in success_patterns)
            has_error = any(p in resp_lower for p in error_patterns)

            if has_success:
                result["success"] = True
                result["reason"] = "送信成功（確認メッセージ検出）"
            elif has_error and not has_success:
                result["reason"] = "送信エラー（バリデーション失敗の可能性）"
            else:
                # 確認画面の可能性もある（2段階フォーム）
                result["success"] = True
                result["reason"] = "送信完了（ステータス200）"
        else:
            result["reason"] = f"HTTPエラー: {result['status_code']}"

    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["reason"] = f"HTTPエラー: {e.code} {e.reason}"
        logger.warning("フォーム送信HTTPエラー [%s]: %s", contact_url, e)
    except Exception as e:
        result["reason"] = f"送信例外: {e}"
        logger.error("フォーム送信失敗 [%s]: %s", contact_url, e, exc_info=True)

    return result
