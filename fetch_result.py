#!/usr/bin/env python3
"""
Numbers4 当選番号自動収集スクリプト
取得元: みずほ銀行 過去当選番号ページ
GitHub Actions にて月〜金 19:15 JST に自動実行
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
}

MIZUHO_BASE = (
    "https://www.mizuhobank.co.jp"
    "/takarakuji/check/numbers/backnumber/num{n}.html"
)
FALLBACK_URL = "https://numbers4.money-plan.net/"


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            for enc in ("utf-8", "shift_jis", "euc-jp"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        log(f"    HTTP {e.code}: {url}")
    except Exception as e:
        log(f"    エラー: {e}")
    return None


def page_start(round_no):
    return (round_no - 1) // 20 * 20 + 1


def parse_mizuho(html):
    results = []
    seen = set()

    # HTMLタグを除去してテキストだけで解析
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)

    # パターン: 第NNNN回 ... YYYY年M月D日 ... 3桁 ... 4桁
    pat = re.compile(
        r"第\s*(\d{4,5})\s*回\s+"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"[^0-9]{0,20}"
        r"(\d{3})\s+"
        r"(\d{4})"
    )
    for m in pat.finditer(text):
        rnd = int(m.group(1))
        if rnd in seen:
            continue
        seen.add(rnd)
        month = m.group(3).zfill(2)
        day   = m.group(4).zfill(2)
        num   = m.group(6)
        results.append({"round": rnd, "date": f"{month}/{day}", "num": num})

    # HTMLテーブルからも試みる
    if not results:
        tr_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        td_pat = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
        for tr in tr_pat.finditer(html):
            cells = [re.sub(r"<[^>]+>", "", c.group(1)).strip()
                     for c in td_pat.finditer(tr.group(1))]
            if len(cells) < 4:
                continue
            rm = re.search(r"第\s*(\d{4,5})\s*回", cells[0])
            if not rm:
                continue
            dm = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", cells[1])
            if not dm:
                continue
            n4 = None
            for c in reversed(cells):
                if re.fullmatch(r"\d{4}", c.strip()):
                    n4 = c.strip()
                    break
            if n4:
                rnd = int(rm.group(1))
                if rnd not in seen:
                    seen.add(rnd)
                    month = dm.group(2).zfill(2)
                    day   = dm.group(3).zfill(2)
                    results.append({"round": rnd, "date": f"{month}/{day}", "num": n4})

    return results


def parse_fallback(html):
    results = []
    seen = set()
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    pat = re.compile(
        r"第\s*(\d{4,5})\s*回\s+"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"[^0-9]{0,30}"
        r"(\d{4})"
    )
    for m in pat.finditer(text):
        rnd = int(m.group(1))
        if rnd in seen:
            continue
        seen.add(rnd)
        month = m.group(3).zfill(2)
        day   = m.group(4).zfill(2)
        results.append({"round": rnd, "date": f"{month}/{day}", "num": m.group(5)})
        if len(results) >= 30:
            break
    return results


def load_existing():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated": "", "results": []}


def main():
    today = datetime.now(tz=JST).strftime("%Y-%m-%d")
    log(f"[{today}] Numbers4 自動収集 開始")

    existing    = load_existing()
    ex_results  = existing.get("results", [])
    ex_rounds   = {r["round"] for r in ex_results}
    latest_known = max(ex_rounds, default=6900)
    log(f"  既存: {len(ex_results)} 件 / 最新既知: 第{latest_known}回")

    fetched_map = {}

    # 取得するページ数（不足分を補う）
    pages_needed = max(5, (MAX_RESULTS - len(ex_results)) // 20 + 2)
    latest_ps = page_start(latest_known)
    pages = [latest_ps - i * 20 for i in range(pages_needed) if latest_ps - i * 20 >= 1]
    log(f"  みずほ銀行 {len(pages)} ページを取得")

    mizuho_ok = False
    for i, ps in enumerate(pages):
        url  = MIZUHO_BASE.format(n=ps)
        html = fetch(url)
        if html:
            parsed = parse_mizuho(html)
            log(f"    num{ps}.html → {len(parsed)} 件")
            if parsed:
                mizuho_ok = True
                for r in parsed:
                    fetched_map[r["round"]] = r
        time.sleep(1.0)
        if len(fetched_map) >= MAX_RESULTS:
            break

    if not mizuho_ok or len(fetched_map) < 3:
        log("  フォールバック取得中...")
        html = fetch(FALLBACK_URL)
        if html:
            parsed = parse_fallback(html)
            log(f"    フォールバック → {len(parsed)} 件")
            for r in parsed:
                if r["round"] not in fetched_map:
                    fetched_map[r["round"]] = r

    if not fetched_map:
        log("  [WARN] データ取得失敗。既存データを維持します。")
        sys.exit(0)

    # マージ
    combined = {r["round"]: r for r in ex_results}
    combined.update(fetched_map)
    merged = sorted(combined.values(), key=lambda r: r["round"], reverse=True)[:MAX_RESULTS]

    latest_r = merged[0]["round"]
    log(f"  合計: {len(merged)} 件 / 最新: 第{latest_r}回 {merged[0]['num']}")

    # 変更がなければスキップ
    if (existing.get("updated") == today
            and len(ex_results) == len(merged)
            and ex_results[:3] == merged[:3]):
        log("  変更なし。スキップ。")
        sys.exit(0)

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
