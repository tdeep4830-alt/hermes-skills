import argparse
import json
import os
import yfinance as yf
from tg_notify import send

def get_price(ticker: str):
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
        "ticker": ticker,
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "volume": info.get("volume"),
        "avg_volume": info.get("averageVolume"),
        "currency": info.get("currency", "USD")
    }

def check_volume_alert(data: dict, threshold: float = 1.5):
    """成交量超過平均 1.5x 即異常"""
    if data["volume"] and data["avg_volume"]:
        ratio = data["volume"] / data["avg_volume"]
        if ratio >= threshold:
            send(f"🔊 *成交量異常*\n{data['ticker']} 成交量 {ratio:.1f}x 平均水平\n現價: ${data['price']:.2f}")
            return True
    return False

def check_target_alert(ticker: str, current_price: float):
    """檢查係咪突破目標價"""
    target_file = os.path.expanduser(f"~/.hermes/data/targets/{ticker}.json")
    if not os.path.exists(target_file):
        return
    with open(target_file) as f:
        target = json.load(f)["target"]
    if current_price >= target:
        send(f"🎯 *目標價突破*\n{ticker} 現價 ${current_price:.2f} 已突破目標 ${target:.2f}")

def set_target(ticker: str, target: float):
    import pathlib
    d = pathlib.Path(os.path.expanduser("~/.hermes/data/targets"))
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{ticker}.json", "w") as f:
        json.dump({"ticker": ticker, "target": target}, f)
    print(f"✅ {ticker} 目標價設定為 ${target}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["price", "volume", "set-target"], required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--target", type=float)
    args = parser.parse_args()

    if args.action == "set-target":
        set_target(args.ticker.upper(), args.target)
        return

    data = get_price(args.ticker.upper())

    if args.action == "volume":
        check_volume_alert(data)

    if args.action == "price":
        check_target_alert(args.ticker.upper(), data["price"])

    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()