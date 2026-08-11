---
name: news-pipeline-scheduled-jobs
description: Run or schedule the two background jobs for the financial news pipeline — the periodic News-Company / News-Article matching job, and the daily auto-fetch job (RSS/HN/Finnhub → clean/dedup → load → concept extraction → embedding). Use when the user asks to run/schedule matching, or asks about the daily market-news auto-fetch job / cron job / Mind Map accumulation. Not for ingesting a specific news article or text the user pastes — see the `news-article-ingest` skill for that.
---

# News Pipeline — Scheduled / Background Jobs

操作 `financial_news_article` project 嘅兩個背景/定時工作：**Matching** 同
**每日自動 Fetch Job**。兩個都係自主觸發，唔靠用戶貼原文——想 ingest 用戶貼嘅
新聞/文章，用 `news-article-ingest` 呢個 skill。

## 執行方式

所有指令透過 Hermes API Bridge（`http://172.16.1.1:5000`）執行。
**Agent 只需用 `curl` 呼叫 API，唔需要、亦唔應該直接行 `docker` 或 `python` 指令。**


## Run Matching

```bash
curl -s -X POST http://172.16.1.1:5000/run/run-matching
```

跑：
- `match_news_to_companies()` —— Layer 2（tag/category）+ Layer 3（embedding）
- `match_news_to_articles()` —— shared_company / shared_tag / embedding 三層

兩個 matcher 都係 idempotent，隨時可以重跑。

**幾時跑**：
- 用戶明確講「跑吓 matching」
- 用戶一次過貼咗好幾則新聞/文章後問相關性（見 `news-article-ingest` skill）
- 生產環境用 cron 定時觸發（建議每 15-60 分鐘）

## 每日自動 Fetch Job

唔靠用戶餵料，自己去 RSS（TechCrunch/The Verge/Ars Technica/VentureBeat/MIT Tech
Review）+ Hacker News + Finnhub 攞返最新 AI/Tech 新聞，一路做到寫入 Mind Map。

單一個 entrypoint，一個 API call 走晒成條鏈（fetch → clean/dedup → load →
concept extraction → embedding）：

```bash
curl -s -X POST http://172.16.1.1:5000/run/daily-fetch
```

> ⚠️ 呢個 skill 假設 Flask API Bridge 已經有 `/run/daily-fetch` 呢條 route，
> 內部對應執行 `python -m app.etl.run_daily`（同 `/run/run-matching` 果類 route
> 做法一樣）。Bridge server 唔喺呢個 repo 入面（喺 host 機獨立跑），如果呢條
> route 仲未加，要先喺 bridge server 度加返（照抄 `/run/run-matching` 嗰個
> handler，改行 `python -m app.etl.run_daily` 就得），先可以用呢個 skill。

背後做（`app/etl/run_daily.py` 嘅 `if __name__ == "__main__":`）：
1. `daily_news_fetch()` —— fetch 三類來源 → relevance filter + dedup（url exact
   match + title 相似度）→ 配對已知公司 → 寫入 `news` table（靠 `News.url`
   exact match 判斷「已存在就跳過」，所以可以放心隔日/隔幾個鐘重跑，唔會插重複）
2. `run_process_news_for_concepts()` —— **淨係**對呢次先新插入嘅新聞（唔包
   skipped_existing 嗰啲），逐條抽 theme/relation 寫入 Mind Map（Concept Graph）
3. `embedding_job()` —— batch 補返漏低嘅 company facts / news / article embedding

## 建議 Cron 排程

生產環境建議用 host 機 crontab 觸發，一樣經 Flask API（唔好喺 cron 度直接
`docker exec`，同 Agent 用嗰條路徑保持一致），例如每日一次：

```cron
0 6 * * * curl -s -X POST http://172.16.1.1:5000/run/daily-fetch >> /var/log/hermes-daily-fetch.log 2>&1
```

**點解淨係一日一次（唔似 matching 嗰邊建議 15-60 分鐘一次）**：
- RSS/HN 呢類編輯精選 feed 更新頻率本身唔高，一日跑幾次邊際新增內容好少
- 每條新插入嘅新聞都會觸發一次 LLM concept extraction（DeepSeek）+ 至少一次
  embedding API call（OpenAI）——跑得越密，call 得越多，一日一次已經夠追市場
- 想追更即時嘅新聞，應該用 `news-article-ingest` 嗰個 skill（用戶/TG 主動貼），
  唔好淨係谷呢個 job 嘅頻率

## 確認 API 正常運行

```bash
curl -s http://172.16.1.1:5000/health
```

返回 `{"status": "ok"}` 即係正常。

## 注意事項

- **環境變數**：container 入面要有 `DATABASE_URL`、`OPENAI_API_KEY`（DeepSeek key）、
  `EMBEDDING_API_KEY`（OpenAI key）。
- **每日 Fetch Job 唔使跑 matching**：`daily_news_fetch()` 寫入新聞嗰陣已經即時
  配對咗公司（`clean_news.py` 嘅關鍵字/ticker matching），唔靠「Run Matching」
  嗰套 embedding-based matcher；跑埋「每日 Fetch Job」唔代表可以慳返「Run
  Matching」——兩者處理緊唔同來源嘅新聞（呢個淨係 RSS/HN/Finnhub 自動攞返嚟嘅，
  唔係用戶貼嘅），仍然要分開排程跑。
- **Ingestion 時機**：如果用戶貼咗新聞/文章想即刻 ingest，用 `news-article-ingest`
  呢個 skill，唔係呢度嘅任何一個 job。
