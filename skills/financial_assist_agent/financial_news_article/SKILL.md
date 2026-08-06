---
name: news-article-ingest-and-matching
description: Ingest a piece of financial news or an analysis article into the database pipeline (LLM extraction, auto company creation, embedding), and run the periodic News-Company / News-Article matching job. Use when the user pastes raw news text or an analysis article — including in the Telegram group chat — and wants it added to the database, or asks to run/schedule matching.
---

# Stock News DB Pipeline

操作 `financial_news_article` project 嘅新聞/文章 ingestion + matching pipeline。

## 執行方式

所有指令透過 Hermes API Bridge（`http://127.0.0.1:5000`）執行，
由 host 機負責 `docker exec` 入 `hermes-agent-c05p-hermes-agent-1` container。
Agent 只需用 `curl` 呼叫即可，唔需要直接執行 python。

## TG Group trigger

用戶（或任何人）喺 Telegram Group 貼一段新聞原文或分析文章，
**唔使用戶再明確講「幫我存入 DB」**——見到成段似新聞/文章嘅內容
（唔係普通聊天、唔係問價、唔係短短一兩句評論），就即刻自動判斷
News 定 Article pipeline，然後直接 call API，唔使再等用戶確認。
唔肯定係咪想 ingest（例如淨係分享個 link 加幾句評論），先問用戶一聲。

## 點分 News 同 Article

| | News | Article |
|---|---|---|
| 內容 | 報導「發生咗咩事」| 作者有論點、推論、投資觀點 |
| 例子 | 業績、產品發布、併購 | Seeking Alpha 風格長文 |

唔肯定就問用戶。

## Ingest News

```bash
curl -s -X POST http://127.0.0.1:5000/run/ingest-news \
  -H "Content-Type: application/json" \
  -d '{"text": "新聞原文貼呢度", "source": "Reuters"}'
```

背後自動做：
1. LLM 抽 title/description/sentiment/tickers/tags
2. 逐個 ticker 查 DB；搵唔到就自動建公司（yfinance + SEC 10-K）
3. 存入 `news` table + `NewsCompanyLink` + tag
4. embed 落 `company_fact_embeddings`

成功會返回 `"已存入 DB：news_id=<id>"`。

## Ingest Article

```bash
curl -s -X POST http://127.0.0.1:5000/run/ingest-article \
  -H "Content-Type: application/json" \
  -d '{"text": "文章原文貼呢度", "source": "Seeking Alpha"}'
```

同 News pipeline，額外存 `thesis`/`conclusion`（`analysis_article` table），
用 `ArticleCompanyLink`/`ArticleTagLink`，多 embed 一次 `thesis`。

## Run Matching

**唔好**每次 ingest 完即刻跑，應該等一批新聞/文章入晒 DB 先一齊跑：

```bash
curl -s -X POST http://127.0.0.1:5000/run/run-matching
```

跑：
- `match_news_to_companies()` —— Layer 2（tag/category）+ Layer 3（embedding）
- `match_news_to_articles()` —— shared_company / shared_tag / embedding 三層

兩個 matcher 都係 idempotent，隨時可以重跑。

**幾時跑**：
- 用戶明確講「跑吓 matching」
- 用戶一次過貼咗好幾則新聞/文章後問相關性
- 生產環境用 cron 定時觸發（建議每 15-60 分鐘）

## 確認 API 正常運行

```bash
curl -s http://127.0.0.1:5000/health
```

返回 `{"status": "ok"}` 即係正常。

## 注意事項

- **成本**：每次 ingest call 幾次 DeepSeek + OpenAI embedding。
  用戶一次貼十幾則嘢想全部 ingest，先確認再執行。
- **環境變數**：container 入面要有 `DATABASE_URL`、`OPENAI_API_KEY`（DeepSeek key）、
  `EMBEDDING_API_KEY`（OpenAI key）。
- **Matching 時機**：ingestion 同 matching 分開 cadence，唔好 ingest 一次就即刻跑。