"""koMidori 営業文（固定テンプレート）"""

SUBJECT = "口コミ返信、AIで自動化しませんか？"

BODY = """\
突然のご連絡失礼いたします。
koMidori（こみどり）の千葉と申します。

飲食店・美容院・クリニックなど地域のお店向けに、
Google口コミの返信をAIで自動化するサービスを提供しております。

■ 1日30秒で口コミ対応が完了
■ 月額4,980円〜・契約縛りなし
■ 1週間の無料トライアルあり

詳しくはこちらをご覧ください。
https://komidori.com

ご興味がございましたら、お気軽にご連絡ください。

koMidori
info@komidori.com"""


def generate(**_kwargs) -> dict:
    """固定の営業文を返す"""
    return {"subject": SUBJECT, "body": BODY}
