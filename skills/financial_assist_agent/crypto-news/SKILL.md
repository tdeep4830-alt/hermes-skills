---
name: btc-news-fetcher
description: >
  每日自動從網路抓取最新 Bitcoin (BTC) 新聞，整理成摘要並可透過 Telegram 發送通知。
  當使用者提到「BTC 新聞」、「比特幣新聞」、「抓取加密貨幣新聞」、「每日 BTC 報告」時觸發此 Skill。
  支援多個新聞來源（CoinDesk、CoinTelegraph、The Block、Bitcoin Magazine），
  輸出包含標題、摘要、來源連結，並依重要性排序。
---

# BTC 新聞抓取 Skill

## 功能概覽

每日從多個主流加密貨幣新聞來源抓取最新 BTC 相關新聞，整理成結構化摘要，
可選擇性地透過 Telegram Bot 推送通知。

## 使用方式

執行主腳本：
```bash
python3 ~/.hermes/skills/custom/btc-news-fetcher/scripts/fetch_btc_news.py
```

只顯示標題（簡潔模式）：
```bash
python3 ~/.hermes/skills/custom/btc-news-fetcher/scripts/fetch_btc_news.py --brief
```

## 新聞來源

| 來源            | RSS / API                                      | 說明             |
|----------------|------------------------------------------------|-----------------|
| CoinDesk       | https://www.coindesk.com/arc/outboundfeeds/rss/ | 主流媒體，英文    |
| CoinTelegraph  | https://cointelegraph.com/rss                  | 深度分析，英文    |
| Bitcoin Magazine| https://bitcoinmagazine.com/.rss/full          | BTC 專注，英文   |
| The Block      | https://www.theblock.co/rss.xml                | 機構視角，英文   |

## 輸出格式

```
📰 BTC 每日新聞摘要 — 2025-01-15 08:00

🔥 重點新聞 (今日 Top 5)
━━━━━━━━━━━━━━━━━━━━━━
1. [標題]
   📌 來源: CoinDesk | ⏰ 2小時前
   📝 摘要: ...
   🔗 連結: https://...

2. ...

📊 今日 BTC 價格快照
   💰 現價: $XXX,XXX
   📈 24h 變動: +X.XX%
```


## 依賴套件安裝

```bash
pip3 install feedparser requests python-dotenv --break-system-packages
```

## 腳本位置

主腳本：`scripts/fetch_btc_news.py`

詳見腳本內的注解說明各功能區塊。
