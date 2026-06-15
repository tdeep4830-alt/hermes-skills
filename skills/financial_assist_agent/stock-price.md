---
name: stock-price
description: 查詢美股(NYSE/NASDAQ)實時價格、歷史數據、成交量異常偵測及目標價突破警報
version: 1.0.0
metadata:
  hermes:
    tags: [finance, stock, NYSE, NASDAQ]
    category: financial-assistant
    requires_toolsets: [terminal]
---

# Stock Price Agent

## When to Use
用戶查詢美股價格、成交量、或需要設定目標價警報時觸發。

## Procedure
1. 分析用戶需求（查價/設目標價/查成交量）
2. 執行對應指令
3. 回傳結果並格式化顯示

## Commands
查詢股價：
`python3 ~/.hermes/financial_assist_agent/scripts/stock_price.py --action price --ticker AAPL`

設定目標價：
`python3 ~/.hermes/financial_assist_agent/scripts/stock_price.py --action set-target --ticker AAPL --target 200`

查成交量：
`python3 ~/.hermes/financial_assist_agent/scripts/stock_price.py --action volume --ticker AAPL`

## Output Format
返回 JSON：
{"ticker": "AAPL", "price": 195.5, "volume": 12000000, "alert": false}

## Pitfalls
