import argparse
import json
import os
from datetime import datetime
from stock_price import get_price as get_stock
from crypto_price import get_price as get_crypto
from tg_notify import send

# Supabase 之後接入，暫時用本地 JSON
PORTFOLIO_FILE = os.path.expanduser("~/.hermes/data/portfolio.json")

def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return {"stocks": [], "crypto": []}
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)

def save_portfolio(data):
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)

def add_position(type: str, ticker: str, shares: float, cost: float):
    portfolio = load_portfolio()
    entry = {"ticker": ticker, "shares": shares, "cost_per_unit": cost}
    portfolio[type + "s"].append(entry)
    save_portfolio(portfolio)
    print(f"✅ 已加入 {ticker} x{shares} @ ${cost}")

def daily_summary():
    portfolio = load_portfolio()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"📊 *每日投資組合總結*\n_{now}_\n"]

    total_cost = 0
    total_value = 0

    # 股票
    if portfolio["stocks"]:
        lines.append("*📈 美股*")
        for s in portfolio["stocks"]:
            data = get_stock(s["ticker"])
            price = data["price"]
            value = price * s["shares"]
            cost = s["cost_per_unit"] * s["shares"]
            pnl = value - cost
            pnl_pct = (pnl / cost) * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} {s['ticker']}: ${price:.2f} "
                f"| {pnl_pct:+.1f}% "
                f"| PnL: ${pnl:+.0f}"
            )
            total_cost += cost
            total_value += value

    # 加密貨幣
    if portfolio["crypto"]:
        lines.append("\n*₿ 加密貨幣*")
        coin_map = {"BTC": "bitcoin", "ETH": "ethereum"}
        for c in portfolio["crypto"]:
            coin_id = coin_map.get(c["ticker"], c["ticker"].lower())
            data = get_crypto(coin_id)
            price = data["price"]
            change = data["change_24h"]
            value = price * c["shares"]
            cost = c["cost_per_unit"] * c["shares"]
            pnl = value - cost
            pnl_pct = (pnl / cost) * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} {c['ticker']}: ${price:,.0f} "
                f"| 24h: {change:+.1f}% "
                f"| PnL: ${pnl:+.0f}"
            )
            total_cost += cost
            total_value += value

    # 總結
    total_pnl = total_value - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0
    lines.append(f"\n*總覽*")
    lines.append(f"總值: ${total_value:,.0f}")
    lines.append(f"總盈虧: ${total_pnl:+,.0f} ({total_pct:+.1f}%)")

    send("\n".join(lines))
    print("✅ 已發送每日總結")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["daily-summary", "portfolio", "add"], required=True)
    parser.add_argument("--type", choices=["stock", "crypto"])
    parser.add_argument("--ticker")
    parser.add_argument("--shares", type=float)
    parser.add_argument("--cost", type=float)
    args = parser.parse_args()

    if args.action == "daily-summary":
        daily_summary()
    elif args.action == "portfolio":
        print(json.dumps(load_portfolio(), indent=2))
    elif args.action == "add":
        add_position(args.type, args.ticker.upper(), args.shares, args.cost)

if __name__ == "__main__":
    main()