#!/usr/bin/env python3
"""
crypto_price.py — 優化版
==========================
改動重點：
  - 格式化報告由 Python 直接完成（0 tokens）
  - 新增 --action report：輸出完整 Telegram 報告，只用 AI 寫一句評語
  - 新增 --no-ai：完全唔用 AI，純 Python 輸出（最慳）
  - 舊有 price / set-target / change 指令保持不變，向下相容

用法：
  python3 crypto_price.py --action price --coin bitcoin
  python3 crypto_price.py --action report --coin bitcoin
  python3 crypto_price.py --action report --coin bitcoin --no-ai
  python3 crypto_price.py --action report --coin bitcoin --telegram
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 依賴 ────────────────────────────────────────────────────────────────────
try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("❌ pip3 install requests python-dotenv --break-system-packages")
    sys.exit(1)

# ── 設定 ────────────────────────────────────────────────────────────────────
ENV_PATH = Path.home() / ".hermes" / "config" / "btc_news.env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

TARGET_FILE = Path.home() / ".hermes" / "data" / "crypto_targets.json"
TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids={coin}&vs_currencies=usd"
    "&include_24hr_change=true&include_24hr_vol=true"
    "&include_market_cap=true"
)

# OpenRouter / Kimi K2.6 設定
OPENROUTER_URL    = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY    = os.getenv("OPENROUTER_API_KEY", "")
AI_MODEL          = os.getenv("AI_MODEL", "moonshotai/kimi-k2:free")

COIN_DISPLAY = {
    "bitcoin":  {"symbol": "BTC", "emoji": "₿"},
    "ethereum": {"symbol": "ETH", "emoji": "Ξ"},
}


# ── 資料抓取 ─────────────────────────────────────────────────────────────────

def fetch_price(coin: str) -> dict:
    """從 CoinGecko 抓取價格資料。"""
    try:
        r = requests.get(COINGECKO_URL.format(coin=coin), timeout=10)
        r.raise_for_status()
        data = r.json().get(coin, {})
        return {
            "coin":       coin,
            "price":      data.get("usd", 0),
            "change_24h": data.get("usd_24h_change", 0),
            "volume_24h": data.get("usd_24h_vol", 0),
            "market_cap": data.get("usd_market_cap", 0),
        }
    except Exception as e:
        print(f"❌ 無法取得 {coin} 價格: {e}", file=sys.stderr)
        sys.exit(1)


def load_targets() -> dict:
    if TARGET_FILE.exists():
        return json.loads(TARGET_FILE.read_text())
    return {}


def save_targets(targets: dict):
    TARGET_FILE.write_text(json.dumps(targets, indent=2))


def check_target(coin: str, price: float) -> dict | None:
    """檢查是否達到目標價，回傳目標資訊或 None。"""
    targets = load_targets()
    if coin not in targets:
        return None
    target = targets[coin]
    hit = (
        (target["direction"] == "above" and price >= target["price"]) or
        (target["direction"] == "below" and price <= target["price"])
    )
    return {"target_price": target["price"], "hit": hit, "direction": target["direction"]}


# ── AI 評語（唯一需要 token 的地方）────────────────────────────────────────────

def get_ai_commentary(coin: str, price: float, change_24h: float) -> str:
    """
    只把三個數字交畀 AI，要求回傳一句話評語。
    Input tokens 極少（~50 tokens），output 限制 60 tokens。
    """
    if not OPENROUTER_KEY:
        return "（未設定 AI API Key，略過評語）"

    symbol = COIN_DISPLAY.get(coin, {}).get("symbol", coin.upper())
    sign   = "+" if change_24h >= 0 else ""
    prompt = (
        f"{symbol} 現價 ${price:,.0f}，24h {sign}{change_24h:.1f}%。"
        f"用一句話（繁體中文，20字內）評論當前市況，語氣簡潔專業。"
    )

    try:
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      AI_MODEL,
                "max_tokens": 60,          # ← 嚴格限制 output
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"（AI 評語失敗: {e}）"


# ── 格式化輸出（Pure Python，0 tokens）──────────────────────────────────────

def format_report(data: dict, target_info: dict | None, commentary: str) -> str:
    """Python 直接格式化，完全唔需要 AI。"""
    coin    = data["coin"]
    meta    = COIN_DISPLAY.get(coin, {"symbol": coin.upper(), "emoji": "🪙"})
    symbol  = meta["symbol"]
    emoji   = meta["emoji"]
    price   = data["price"]
    chg     = data["change_24h"]
    vol     = data["volume_24h"]
    mcap    = data["market_cap"]

    arrow   = "📈" if chg >= 0 else "📉"
    sign    = "+" if chg >= 0 else ""
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{emoji} {symbol} 市況報告 — {now}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 現價:      ${price:>12,.2f} USD",
        f"{arrow} 24h 變動:  {sign}{chg:.2f}%",
        f"📊 24h 成交量: ${vol / 1e9:.2f}B",
        f"🏦 市值:       ${mcap / 1e9:.1f}B",
    ]

    # 目標價狀態
    if target_info:
        tp = target_info["target_price"]
        if target_info["hit"]:
            lines.append(f"\n🎯 目標價 ${tp:,.0f} 已達到！")
        else:
            direction_zh = "突破" if target_info["direction"] == "above" else "跌破"
            diff_pct = abs(price - tp) / tp * 100
            lines.append(f"\n🎯 目標價: ${tp:,.0f}（距離{direction_zh} {diff_pct:.1f}%）")

    # AI 評語（只有呢一行係 AI 生成）
    if commentary:
        lines.append(f"\n💬 {commentary}")

    lines.append("─────────────────────────")
    return "\n".join(lines)


# ── Telegram 發送 ─────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("❌ 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=15,
    )
    if r.ok:
        print("✅ Telegram 已發送")
    else:
        print(f"❌ Telegram 失敗: {r.text}", file=sys.stderr)
    return r.ok


# ── Actions ───────────────────────────────────────────────────────────────────

def action_price(args):
    """原有 price 指令，保持 JSON 輸出（向下相容）。"""
    data   = fetch_price(args.coin)
    target = check_target(args.coin, data["price"])
    result = {
        "coin":       data["coin"],
        "price":      data["price"],
        "change_24h": round(data["change_24h"], 2),
        "target_hit": target["hit"] if target else False,
    }
    print(json.dumps(result))


def action_change(args):
    """原有 change 指令，保持 JSON 輸出（向下相容）。"""
    data = fetch_price(args.coin)
    print(json.dumps({
        "coin":       data["coin"],
        "change_24h": round(data["change_24h"], 2),
        "volume_24h": data["volume_24h"],
    }))


def action_set_target(args):
    """設定目標價，純 Python 邏輯。"""
    targets = load_targets()
    direction = "above" if args.target > 0 else "below"
    targets[args.coin] = {
        "price":     abs(args.target),
        "direction": direction,
        "set_at":    datetime.now().isoformat(),
    }
    save_targets(targets)
    symbol = COIN_DISPLAY.get(args.coin, {}).get("symbol", args.coin.upper())
    print(f"✅ 已設定 {symbol} 目標價: ${abs(args.target):,.0f} ({direction})")


def action_report(args):
    """
    新增：完整報告模式。
    Python 格式化 + 可選 AI 一句評語 + 可選 Telegram 發送。
    """
    data       = fetch_price(args.coin)
    target     = check_target(args.coin, data["price"])

    # AI 評語（除非 --no-ai）
    commentary = ""
    if not args.no_ai:
        commentary = get_ai_commentary(args.coin, data["price"], data["change_24h"])

    report = format_report(data, target, commentary)
    print(report)

    if args.telegram:
        send_telegram(report)


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto Price Tool（優化版）")
    parser.add_argument("--action", required=True,
                        choices=["price", "change", "set-target", "report"])
    parser.add_argument("--coin",   required=True, help="bitcoin / ethereum")
    parser.add_argument("--target", type=float,    help="目標價（set-target 用）")
    parser.add_argument("--no-ai",  action="store_true", help="唔用 AI，純 Python 輸出")
    parser.add_argument("--telegram", action="store_true", help="發送 Telegram 通知")
    args = parser.parse_args()

    dispatch = {
        "price":      action_price,
        "change":     action_change,
        "set-target": action_set_target,
        "report":     action_report,
    }
    dispatch[args.action](args)


if __name__ == "__main__":
    main()