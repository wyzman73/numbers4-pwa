#!/usr/bin/env python3
"""
Numbers4 当選番号自動取得スクリプト
対象: numbers4.money-plan.net（公開データ）
実行タイミング: GitHub Actions により月〜金 19:15 JST に自動実行
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data.json"
URL = "https://numbers4.money-plan.net/"
MAX_RESULTS = 20

# 曜日マッピング
WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; Numbers4PWA/1.0; "
                "+https://github.com)"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_results(html: str) -> list[dict]:
    """
    テーブルから回号・日付・当選番号を抽出する。
    対象行の例:
      第6980回  2026年5月11日(月)  8852
    """
    # 回号と当選番号を含む行を正規表現で抜き出す
    pattern = re.compile(
        r"第(\d+)回.*?(\d{4})年(\d{1,2})月(\d{1,2})日[（(]([月火水木金土日])[）)]"
        r".*?\|\s*\[?\*?\*?(\d{4})\*?\*?\]?",
        re.DOTALL,
    )

    # より単純なアプローチ: テーブル行からデータを取る
    # 「第XXXX回 | YYYY年M月D日(曜) | NNNN」パターンを探す
    row_pattern = re.compile(
        r"第(\d{4,5})回.*?"           # 回号
        r"(\d{4})年(\d{1,2})月(\d{1,2})日[（(]([月火水木金土日])[）)]"  # 日付
        r".*?"
        r"\|\s*\[?\*?\*?(\d{4})\*?\*?\]?\s*\|",  # 当選番号
        re.DOTALL,
    )

    results = []
    seen_rounds = set()

    for m in row_pattern.finditer(html):
        round_no = int(m.group(1))
        year     = m.group(2)
        month    = m.group(3).zfill(2)
        day      = m.group(4).zfill(2)
        weekday  = m.group(5)
        num      = m.group(6)

        if round_no in seen_rounds:
            continue
        seen_rounds.add(round_no)

        date_str = f"{month}/{day}{weekday}"
        results.append({"round": round_no, "date": date_str, "num": num})

        if len(results) >= MAX_RESULTS:
            break

    # 回号降順でソート
    results.sort(key=lambda r: r["round"], reverse=True)
    return results


def load_existing() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated": "", "results": []}


def merge_results(existing: list[dict], fetched: list[dict]) -> list[dict]:
    """既存データと新規データをマージ（重複除去・降順・最大20件）"""
    combined = {r["round"]: r for r in existing}
    for r in fetched:
        combined[r["round"]] = r  # 新規データで上書き
    merged = sorted(combined.values(), key=lambda r: r["round"], reverse=True)
    return merged[:MAX_RESULTS]


def main():
    jst = timezone(timedelta(hours=9))
    today = datetime.now(tz=jst).strftime("%Y-%m-%d")

    print(f"[{today}] Numbers4 当選番号取得開始...")

    # 既存データ読み込み
    existing_data = load_existing()
    existing_results = existing_data.get("results", [])
    print(f"  既存データ: {len(existing_results)} 件")

    # HTML取得
    try:
        html = fetch_html(URL)
        print(f"  HTML取得成功: {len(html):,} bytes")
    except Exception as e:
        print(f"  [ERROR] HTML取得失敗: {e}", file=sys.stderr)
        # 取得失敗でもexitコードを0にして既存データを保持
        sys.exit(0)

    # パース
    fetched = parse_results(html)
    print(f"  パース結果: {len(fetched)} 件")

    if not fetched:
        print("  [WARN] 当選番号が取得できませんでした。既存データを維持します。")
        sys.exit(0)

    # マージ
    merged = merge_results(existing_results, fetched)
    latest_round = merged[0]["round"] if merged else "—"
    print(f"  最新回号: 第{latest_round}回 / 合計 {len(merged)} 件")

    # 変更確認
    if (
        existing_data.get("updated") == today
        and existing_results == merged
    ):
        print("  変更なし。スキップ。")
        sys.exit(0)

    # 書き出し
    output = {"updated": today, "results": merged}
    DATA_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  data.json を更新しました（{len(merged)} 件）")


if __name__ == "__main__":
    main()
