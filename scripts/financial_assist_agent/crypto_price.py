import argparse
import json
import os
import requests
from tg_notify import send

COINGECKO_URL = "https://api.coingecko.com/api/v3"

def get_price(coin: str):
    r = requests.get(f"{COINGECKO_URL}/simple/price", params={
        "ids": coin,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_24hr_vol": "true"
    })
    data = r.json()[coin]
    return {
        "coin": coin,
        "price": data["usd"],
        "change_24h": data.get("usd_24h_change", 0),
        "volume_24h": data.get("usd_24h_vol", 0)
    }

def check_alerts(coin: str, data: dict):
    price = data["price"]
    change = data["change_24h"]

    # 目標價檢查
    target_file = os.path.expanduser(f"~/.hermes/data/targets/{coin}.json")
    if os.path.exists(target_file):
        with open(target_file) as f:
            target = json.load(f)["target"]
        if price >= target:
            send(f"🎯 *目標價突破*\n{coin.upper()} 現價 ${price:,.0f} 已突破目標 ${target:,.0f}")

    # 24h 升跌超過 5% 警報
    if abs(change) >= 5:
        emoji = "🚀" if change > 0 else "🔴"
        send(f"{emoji} *大幅波動*\n{coin.upper()} 24h {change:+.1f}%\n現價: ${price:,.0f}")

def set_target(coin: str, target: float):
    import pathlib
    d = pathlib.Path(os.path.expanduser("~/.hermes/data/targets"))
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{coin}.json", "w") as f:
        json.dump({"coin": coin, "target": target}, f)
    print(f"✅ {coin} 目標價設定為 ${target:,.0f}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["price", "change", "set-target"], required=True)
    parser.add_argument("--coin", required=True)
    parser.add_argument("--target", type=float)
    args = parser.parse_args()

    if args.action == "set-target":
        set_target(args.coin, args.target)
        return

    data = get_price(args.coin)
    check_alerts(args.coin, data)
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()