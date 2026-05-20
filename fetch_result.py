#!/usr/bin/env python3
"""
Numbers4 当選番号自動収集スクリプト
取得元（優先順）:
  1. みずほ銀行 過去当選番号ページ（最大100件）
     https://www.mizuhobank.co.jp/takarakuji/check/numbers/backnumber/num{N}.html
  2. フォールバック: numbers4.money-plan.net（直近20件）

GitHub Actions にて月〜金 19:15 JST・日曜 09:00 JST に自動実行
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 設定 ─────────────────────────────────────────────────────────
DATA_FILE   = Path(__file__).parent / "data.json"
MAX_RESULTS = 100
JST         = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Cache-Control": "no-cache",
}

MIZUHO_BASE = (
    "https://www.mizuhobank.co.jp/takarakuji/check/numbers/backnumber/num{n}.html"
)
FALLBACK_URL = "https://numbers4.money-plan.net/"

WEEKDAY_JP = {0:"月",1:"火",2:"水",3:"木",4:"金",5:"土",6:"日"}


# ── ユーティリティ ────────────────────────────────────────────────
def log(msg: str):
    print(msg, flush=True)


def fetch(url: str, timeout: int = 20) -> str | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # みずほ銀行は Shift_JIS の場合あり
            for enc in ("utf-8", "shift_jis", "euc-jp"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        log(f"    HTTP {e.code}: {url}")
    except Exception as e:
        log(f"    エラー: {url} → {e}")
    return None


# ── みずほ銀行ページのパース ──────────────────────────────────────
def page_start(round_no: int) -> int:
    """回号からそのページの開始回号（20の倍数+1）を計算"""
    return (round_no - 1) // 20 * 20 + 1


def parse_mizuho(html: str) -> list[dict]:
    """
    みずほ銀行の生HTMLから回号・日付・N4当選番号を抽出。

    HTML構造例（簡略化）:
      <td>第6961回</td>
      <td>2026年4月14日</td>  ← 曜日なしの場合あり
      <td>XXX</td>            ← ナンバーズ3
      <td>YYYY</td>           ← ナンバーズ4
    """
    results = []
    seen = set()

    # ── アプローチ1: <td>タグを直接マッチ ──────────────────────
    # 「第NNNN回」の直後に日付・N3・N4が続くパターン
    pat1 = re.compile(
        r"第\s*(\d{4,5})\s*回\D{0,30}?"          # 回号
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"  # 年月日
        r"[^<]{0,20}?"                             # 曜日など（任意）
        r"<[^>]*>\s*(\d{3})\s*</[^>]*>"            # N3 (3桁)
        r"\s*<[^>]*>\s*(\d{4})\s*</[^>]*>",        # N4 (4桁)
        re.DOTALL,
    )
    for m in pat1.finditer(html):
        _add_result(results, seen, m.group(1), m.group(3), m.group(4), m.group(6))

    # ── アプローチ2: テーブル行全体を先に抽出してからパース ───
    if not results:
        tr_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        td_pat = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)

        for tr in tr_pat.finditer(html):
            row = tr.group(1)
            cells = [re.sub(r"<[^>]+>", "", c.group(1)).strip()
                     for c in td_pat.finditer(row)]
            if len(cells) < 4:
                continue
            # 第N回 を探す
            round_match = re.search(r"第\s*(\d{4,5})\s*回", cells[0])
            if not round_match:
                continue
            # 日付を探す
            date_match = re.search(
                r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", cells[1]
            )
            if not date_match:
                continue
            # N4は最後の4桁数字セル
            n4 = None
            for cell in reversed(cells):
                if re.fullmatch(r"\d{4}", cell.strip()):
                    n4 = cell.strip()
                    break
            if n4:
                _add_result(results, seen,
                            round_match.group(1),
                            date_match.group(2),
                            date_match.group(3),
                            n4)

    # ── アプローチ3: テキスト行から直接マッチ ─────────────────
    if not results:
        # HTMLタグを除去してテキストだけで解析
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        pat3 = re.compile(
            r"第\s*(\d{4,5})\s*回\s+"
            r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*"
            r"[月火水木金土日（）\(\)]?\s*"
            r"(\d{3})\s+"      # N3
            r"(\d{4})"         # N4
        )
        for m in pat3.finditer(text):
            _add_result(results, seen, m.group(1), m.group(3), m.group(4), m.group(6))

    return results


def _add_result(results, seen, round_str, month_str, day_str, num_str):
    round_no = int(round_str)
    if round_no in seen:
        return
    seen.add(round_no)
    month = month_str.strip().zfill(2)
    day   = day_str.strip().zfill(2)
    results.append({"round": round_no, "date": f"{month}/{day}", "num": num_str.strip()})


# ── フォールバック: numbers4.money-plan.net ───────────────────────
def parse_fallback(html: str) -> list[dict]:
    """
    numbers4.money-plan.net の HTML から抽出。
    複数の正規表現パターンを試みる。
    """
    results = []
    seen = set()

    # パターンA: 「第NNNN回」に続く4桁数字
    pats = [
        # テーブルセル形式
        re.compile(
            r"第(\d{4,5})回[^\d]{5,60}?"
            r"(\d{4})年(\d{1,2})月(\d{1,2})日[^<]{0,15}([月火水木金土日])[^<]{0,5}"
            r".*?[^\d](\d{4})[^\d]",
            re.DOTALL,
        ),
        # シンプル形式
        re.compile(
            r"第(\d{4,5})回[^\d]{0,100}?(\d{4})[^\d]",
            re.DOTALL,
        ),
    ]

    for pat in pats:
        if results:
            break
        for m in pat.finditer(html):
            rnd = int(m.group(1))
            if rnd in seen:
                continue
            # グループ数によって num を取得
            num = m.group(m.lastindex)
            if re.fullmatch(r"\d{4}", num):
                seen.add(rnd)
                results.append({"round": rnd, "date": "—", "num": num})
                if len(results) >= 30:
                    break

    return results


# ── 既存データ ────────────────────────────────────────────────────
def load_existing() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated": "", "results": []}


# ── メイン ───────────────────────────────────────────────────────
def main():
    today = datetime.now(tz=JST).strftime("%Y-%m-%d")
    log(f"[{today}] Numbers4 当選番号自動収集 開始")

    existing = load_existing()
    ex_results = existing.get("results", [])
    ex_rounds  = {r["round"] for r in ex_results}
    log(f"  既存: {len(ex_results)} 件 / 最新既知: 第{max(ex_rounds, default=0)}回")

    # ── 最新回号の推定 ───────────────────────────────────────────
    if ex_rounds:
        latest_known = max(ex_rounds)
    else:
        base = datetime(1994, 10, 7, tzinfo=JST)
        days = (datetime.now(tz=JST) - base).days
        latest_known = max(6900, int(days * 5 / 7))

    # ── みずほ銀行ページを取得 ────────────────────────────────────
    fetched_map: dict[int, dict] = {}

    # 現在のページ + 不足分のページ数を計算
    pages_needed = max(5, (MAX_RESULTS - len(ex_results)) // 20 + 2)
    latest_ps = page_start(latest_known)

    pages = [latest_ps - i * 20 for i in range(pages_needed) if latest_ps - i * 20 >= 1]
    log(f"  取得予定ページ: {pages[:5]}{'...' if len(pages)>5 else ''} ({len(pages)}ページ)")

    mizuho_ok = False
    for i, ps in enumerate(pages):
        url = MIZUHO_BASE.format(n=ps)
        html = fetch(url)
        if html:
            parsed = parse_mizuho(html)
            log(f"    num{ps}.html → {len(parsed)} 件パース")
            if parsed:
                mizuho_ok = True
                for r in parsed:
                    fetched_map[r["round"]] = r
        # 最初の5ページは1秒待ち、それ以降は0.5秒
        time.sleep(1.0 if i < 5 else 0.5)

        # 100件集まったら打ち切り
        if len(fetched_map) >= MAX_RESULTS:
            break

    log(f"  みずほ銀行: {len(fetched_map)} 件取得")

    # ── フォールバック ────────────────────────────────────────────
    if not mizuho_ok or len(fetched_map) < 5:
        log("  フォールバック: numbers4.money-plan.net を試みます")
        html = fetch(FALLBACK_URL)
        if html:
            parsed = parse_fallback(html)
            log(f"    フォールバック → {len(parsed)} 件パース")
            for r in parsed:
                if r["round"] not in fetched_map:
                    fetched_map[r["round"]] = r

    if not fetched_map:
        log("  [WARN] いずれのソースからも取得できませんでした。既存データを維持します。")
        # 日付だけ更新してコミットをスキップ
        sys.exit(0)

    # ── マージ ────────────────────────────────────────────────────
    combined = {r["round"]: r for r in ex_results}
    combined.update(fetched_map)
    merged = sorted(combined.values(), key=lambda r: r["round"], reverse=True)[:MAX_RESULTS]

    new_cnt = len({r for r in fetched_map if r not in ex_rounds})
    latest_r = merged[0]["round"]
    latest_n = merged[0]["num"]
    log(f"  新規: {new_cnt} 件 / 合計: {len(merged)} 件 / 最新: 第{latest_r}回 {latest_n}")

    # ── 変更チェック ─────────────────────────────────────────────
    if (existing.get("updated") == today
            and len(ex_results) == len(merged)
            and ex_results[:5] == merged[:5]):
        log("  変更なし。スキップ。")
        sys.exit(0)

    # ── 書き出し ─────────────────────────────────────────────────
    out = {
        "updated": today,
        "count":   len(merged),
        "latest":  latest_r,
        "results": merged,
    }
    DATA_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"  ✅ data.json 更新完了: {len(merged)} 件")


if __name__ == "__main__":
    main()
