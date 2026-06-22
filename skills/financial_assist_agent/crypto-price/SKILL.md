---
name: crypto-price
description: 查詢BTC、ETH實時價格、24h升跌幅、目標價突破警報
version: 2.0.0
metadata:
  hermes:
    tags: [finance, crypto, bitcoin, ethereum]
    category: financial-assistant
    requires_toolsets: [terminal]
---

# Crypto Price Agent（優化版）

## 優化重點（v1 → v2）
- 格式化報告由 Python 直接完成，**唔再經 AI 處理**
- AI 只寫一句評語（~50 input tokens，output 限 60 tokens）
- 新增 `--no-ai` 模式：完全 0 tokens，適合頻繁 cron job

## Token 消耗對比

| 模式 | Input tokens | Output tokens |
|------|-------------|---------------|
| 舊版（全交 AI）| ~800–1500 | ~200–400 |
| 新版 report | ~50 | ~30 |
| 新版 --no-ai | **0** | **0** |

## When to Use
用戶查詢 BTC 或 ETH 價格、設定目標價、或查看24小時變化時觸發。

## Commands

### 日常查價（向下相容，輸出 JSON）
```
python3 /root/.hermes/scripts/crypto_price.py --action price --coin bitcoin
python3 /root/.hermes/scripts/crypto_price.py --action change --coin ethereum
```

### 完整報告（推薦，含 AI 一句評語）
```
python3 /root/.hermes/scripts/crypto_price.py --action report --coin bitcoin
python3 /root/.hermes/scripts/crypto_price.py --action report --coin ethereum
```

### 完整報告 + 發送 Telegram
```
python3 /root/.hermes/scripts/crypto_price.py --action report --coin bitcoin --telegram
```

### 純 Python 報告（0 tokens，適合頻繁 cron job）
```
python3 /root/.hermes/scripts/crypto_price.py --action report --coin bitcoin --no-ai
python3 /root/.hermes/scripts/crypto_price.py --action report --coin bitcoin --no-ai --telegram
```

### 設定目標價
```
python3 /root/.hermes/scripts/crypto_price.py --action set-target --coin bitcoin --target 120000
```

## Crontab 建議設定

```bash
# 每日早上 8:00 — 帶 AI 評語（每天一次，值得用 token）
0 0 * * * python3 /root/.hermes/scripts/crypto_price.py --action report --coin bitcoin --telegram

# 每 6 小時價格監察 — 純 Python，0 tokens
0 */6 * * * python3 /root/.hermes/scripts/crypto_price.py --action report --coin bitcoin --no-ai --telegram
```

## Output Format

### report 模式輸出範例
```
₿ BTC 市況報告 — 2025-01-15 08:00
━━━━━━━━━━━━━━━━━━━━━━
💰 現價:       $103,500.00 USD
📈 24h 變動:  +2.35%
📊 24h 成交量: $38.50B
🏦 市值:       $2,045.3B
🎯 目標價: $120,000（距離突破 16.0%）

💬 BTC 站穩十萬美元關口，短線情緒偏多。
─────────────────────────
```

### price / change 模式（JSON，向下相容）
```json
{"coin": "bitcoin", "price": 103500, "change_24h": 2.35, "target_hit": false}
```


## Pitfalls
- coin 參數用全名：bitcoin / ethereum
- CoinGecko 免費 API 有 rate limit，頻繁查詢用 --no-ai 避免不必要消耗
- 目標價儲存在 ~/.hermes/data/crypto_targets.json