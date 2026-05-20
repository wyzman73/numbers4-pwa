#!/usr/bin/env python3
"""
Numbers4 当選番号自動取得スクリプト
取得元: みずほ銀行 過去当選番号ページ
  https://www.mizuhobank.co.jp/takarakuji/check/numbers/backnumber/num{N}.html
  ページは 20 回ごとに区切られており、Nは開始回号（20の倍数+1）

実行タイミング:
  GitHub Actions により月〜金 19:15 JST (= 10:15 UTC) に自動実行
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_FILE   = Path(__file__).parent / "data.json"
BASE_URL    = "https://www.mizuhobank.co.jp/takarakuji/check/numbers/backnumber/num{n}.html"
MAX_RESULTS = 100   # data.json に保持する最大件数
FETCH_PAGES = 5     # 初回／不足時に遡るページ数（= 100件）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}
WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


# ── ページ番号の計算 ──────────────────────────────────────────────
def page_start(round_no: int) -> int:
    """回号から、そのページの開始回号を返す（例: 6971→6961, 6981→6981）"""
    return (round_no - 1) // 20 * 20 + 1


# ── HTML 取得 ────────────────────────────────────────────────────
def fetch_page(start_round: int) -> str | None:
    url = BASE_URL.format(n=start_round)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            print(f"    取得: {url} ({len(html):,} bytes)")
            return html
    except Exception as e:
        print(f"    [WARN] {url} 取得失敗: {e}")
        return None


# ── HTML パース ──────────────────────────────────────────────────
def parse_page(html: str) -> list[dict]:
    """
    みずほ銀行ページのテーブルから回号・日付・N4当選番号を抽出。
    テーブル例:
      | 第6961回 | 2026年4月14日(月) | 123 | 4567 |
    マークダウン変換後はパイプ区切りになるが、
    raw HTML も想定して両方の正規表現を試みる。
    """
    results = []
    seen = set()

    # パターン1: Markdown テーブル形式（web_fetch 変換後）
    # | 第NNNNN回 | YYYY年M月D日 | N3 | N4 |
    md_pat = re.compile(
        r"\|\s*第(\d{4,5})回\s*\|\s*(\d{4})年(\d{1,2})月(\d{1,2})日"
        r"[（(（(]?([月火水木金土日])?[）)）)]?\s*\|\s*\d{3}\s*\|\s*(\d{4})\s*\|"
    )
    for m in md_pat.finditer(html):
        round_no = int(m.group(1))
        if round_no in seen:
            continue
        month    = m.group(3).zfill(2)
        day      = m.group(4).zfill(2)
        weekday  = m.group(5) or ""
        num      = m.group(6)
        seen.add(round_no)
        results.append({"round": round_no, "date": f"{month}/{day}{weekday}", "num": num})

    # パターン2: raw HTML の <td> 形式（パターン1で取れなかった場合）
    if not results:
        html_pat = re.compile(
            r"第(\d{4,5})回.*?"
            r"(\d{4})年(\d{1,2})月(\d{1,2})日[^<]{0,10}([月火水木金土日])"
            r".*?<td[^>]*>(\d{3})</td>\s*<td[^>]*>(\d{4})</td>",
            re.DOTALL,
        )
        for m in html_pat.finditer(html):
            round_no = int(m.group(1))
            if round_no in seen:
                continue
            month   = m.group(3).zfill(2)
            day     = m.group(4).zfill(2)
            weekday = m.group(5)
            num     = m.group(7)
            seen.add(round_no)
            results.append({"round": round_no, "date": f"{month}/{day}{weekday}", "num": num})

    return results


# ── 既存データ読み込み ────────────────────────────────────────────
def load_existing() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated": "", "results": []}


# ── メイン ───────────────────────────────────────────────────────
def main():
    jst   = timezone(timedelta(hours=9))
    today = datetime.now(tz=jst).strftime("%Y-%m-%d")
    print(f"[{today}] Numbers4 当選番号自動収集開始")

    existing_data    = load_existing()
    existing_results = existing_data.get("results", [])
    existing_rounds  = {r["round"] for r in existing_results}
    print(f"  既存データ: {len(existing_results)} 件")

    # ── 最新回号の推定 ──────────────────────────────────────────
    # 既存データの最大回号、または今日の日付から概算
    if existing_results:
        latest_known = max(r["round"] for r in existing_results)
    else:
        # 第1回: 1994年10月7日 → 約7,000回程度（週5×約30年）
        base_date  = datetime(1994, 10, 7, tzinfo=jst)
        days_since = (datetime.now(tz=jst) - base_date).days
        weekdays   = int(days_since * 5 / 7)
        latest_known = max(6900, weekdays)

    print(f"  最新既知回号: 第{latest_known}回")

    # ── 取得するページを決定 ─────────────────────────────────────
    # 最新ページから FETCH_PAGES ページ分を取得
    latest_page_start = page_start(latest_known)
    pages_to_fetch = []
    p = latest_page_start
    for _ in range(FETCH_PAGES):
        if p >= 1:
            pages_to_fetch.append(p)
        p -= 20

    # 既存データが少ない場合はさらに遡る
    if len(existing_results) < MAX_RESULTS:
        extra_needed = (MAX_RESULTS - len(existing_results)) // 20 + 1
        for i in range(len(pages_to_fetch), len(pages_to_fetch) + extra_needed):
            next_p = latest_page_start - i * 20
            if next_p >= 1:
                pages_to_fetch.append(next_p)

    print(f"  取得ページ: {pages_to_fetch}")

    # ── ページ取得・パース ───────────────────────────────────────
    fetched_map = {}
    for start in pages_to_fetch:
        html = fetch_page(start)
        if html:
            parsed = parse_page(html)
            print(f"    → {len(parsed)} 件パース")
            for r in parsed:
                fetched_map[r["round"]] = r
        time.sleep(1.0)   # サーバー負荷軽減

    # ── マージ ───────────────────────────────────────────────────
    combined = {r["round"]: r for r in existing_results}
    combined.update(fetched_map)   # 新規で上書き
    merged = sorted(combined.values(), key=lambda r: r["round"], reverse=True)
    merged = merged[:MAX_RESULTS]

    new_count = len(fetched_map) - len(existing_rounds & fetched_map.keys())
    print(f"  新規取得: {new_count} 件 / 合計: {len(merged)} 件")

    if not merged:
        print("  [WARN] データが取得できませんでした。既存データを維持します。")
        sys.exit(0)

    latest_round = merged[0]["round"]
    print(f"  最新回号: 第{latest_round}回 ({merged[0]['num']})")

    # ── 変更確認 ─────────────────────────────────────────────────
    if (
        existing_data.get("updated") == today
        and len(existing_results) == len(merged)
        and existing_results == merged
    ):
        print("  変更なし。スキップ。")
        sys.exit(0)

    # ── 書き出し ─────────────────────────────────────────────────
    output = {
        "updated": today,
        "count":   len(merged),
        "latest":  latest_round,
        "results": merged,
    }
    DATA_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  ✅ data.json を更新しました（{len(merged)} 件）")


if __name__ == "__main__":
    main()
