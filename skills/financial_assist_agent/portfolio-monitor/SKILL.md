---
name: portfolio-monitor
description: 監控整個投資組合（美股+加密貨幣），每日早晨Telegram總結，目標價及成交量異常警報
version: 1.0.0
metadata:
  hermes:
    tags: [finance, portfolio, monitor, telegram]
    category: financial-assistant
    requires_toolsets: [terminal]
---

# Portfolio Monitor Agent

## When to Use
用戶要求查看整體投資組合、設定警報、或每日總結時觸發。

## Procedure
1. 從 Supabase 讀取持倉數據
2. 調用 stock_price.py 同 crypto_price.py
3. 計算盈虧
4. 檢查警報條件
5. 發送 Telegram 通知

## Commands
每日總結：
`python3 ~/.hermes/scripts/portfolio_monitor.py --action daily-summary`

查看持倉：
`python3 ~/.hermes/scripts/portfolio_monitor.py --action portfolio`

新增持倉：
`python3 ~/.hermes/scripts/portfolio_monitor.py --action add --type stock --ticker AAPL --shares 10 --cost 180`

## Pitfalls
- 需要先設定 Supabase 及 Telegram Bot Token
- 每日總結由 Hermes cron 自動觸發，唔需要手動執行