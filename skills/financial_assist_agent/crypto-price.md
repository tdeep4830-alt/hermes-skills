---
name: crypto-price
description: 查詢BTC、ETH實時價格、24h升跌幅、目標價突破警報
version: 1.0.0
metadata:
  hermes:
    tags: [finance, crypto, bitcoin, ethereum]
    category: financial-assistant
    requires_toolsets: [terminal]
---

# Crypto Price Agent

## When to Use
用戶查詢 BTC 或 ETH 價格、設定目標價、或查看24小時變化時觸發。

## Procedure
1. 識別用戶想查嘅幣種（BTC/ETH）
2. 執行對應指令
3. 格式化回傳結果

## Commands
查詢價格：
`python3 ~/.hermes/financial_assist_agent/scripts/crypto_price.py --action price --coin bitcoin`

設定目標價：
`python3 ~/.hermes/financial_assist_agent/scripts/crypto_price.py --action set-target --coin bitcoin --target 100000`

查24h變化：
`python3 ~/.hermes/financial_assist_agent/scripts/crypto_price.py --action change --coin ethereum`

## Output Format
{"coin": "bitcoin", "price": 67000, "change_24h": 2.5, "target_hit": false}

## Pitfalls
- coin 參數用全名：bitcoin