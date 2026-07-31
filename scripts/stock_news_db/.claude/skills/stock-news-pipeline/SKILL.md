---
name: stock-news-pipeline
description: Ingest a piece of financial news or an analysis article into the stock_news_db pipeline (LLM extraction, auto company creation, embedding), and run the periodic News-Company / News-Article matching job. Use when the user pastes raw news text or an analysis article and wants it added to the database, or asks to run/schedule matching.
---

# Stock News DB Pipeline

操作 `stock_news_db` 呢個 project 嘅新聞/文章 ingestion + matching pipeline。成個 project 喺
`Financial_bot/stock_news_db/`，所有指令都要喺呢層目錄底下用 `python -m app.xxx` 咁執行
（唔可以喺 `app/` 入面執行，`-m` 要求 `app` package 嘅上一層做 cwd）。

## 呢個 skill 覆蓋兩條分開嘅 pipeline，唔好撈埋

1. **News pipeline** —— 用戶貼一段「事實/事件報導」（業績、產品發布、併購、監管動作……），想存入 DB
2. **Article pipeline** —— 用戶貼一篇「分析/觀點文章」，有明確 thesis（投資論點）同 conclusion（例如 Seeking Alpha 風格嘅長文），想存入 DB

點分：如果段文字主要係報導緊「發生咗咩事」，用 News；如果段文字有作者自己嘅論點、推論、
「我認為呢隻股會點點點」，用 Article。唔肯定就問用戶。

## News pipeline

```bash
cd stock_news_db
python -m app.cli ingest-news --file /path/to/news.txt [--source "Reuters"] [--url "https://..."]
```

或者直接 pipe 文字（唔使開檔案）：

```bash
echo "新聞原文……" | python -m app.cli ingest-news --source "Reuters"
```

背後自動做晒（唔使你手動介入，純粹解釋畀你知系統做緊咩）：

1. LLM（`app/etl/LLM_analyze.py`，DeepSeek）由原文抽 title/description/sentiment/tickers/tags
2. 逐個 ticker 查 DB；搵唔到就自動 `save_company()`——攞 yfinance + SEC 10-K 資料，再用 LLM
   拆做 business_model/products/technologies/services/risks/legal_issues 等，一次過起哂間新公司
3. News 存入 `news` table，連埋 `NewsCompanyLink`（match_source='direct_mention'）同 tag
4. 呢則新聞嘅 description 即時 embed 落 `company_fact_embeddings`（entity_type='news'）

成功會印 `已存入 DB：news_id=<id>`。

## Article pipeline

```bash
python -m app.cli ingest-article --file /path/to/article.txt [--source "..."] [--url "..."]
```

同 News pipeline 一樣嘅步驟，加多幾樣：連 `thesis`/`conclusion` 都存埋（`analysis_article`
table），連公司/tag 用 `ArticleCompanyLink`/`ArticleTagLink`（唔係 `NewsCompanyLink`），除咗
embed 底層嗰行 news 嘅 description，仲會多 embed 多次 `thesis`（entity_type='article'）。

## Matching —— 定時/一批一齊做，唔好逐次 ingest 完即刻跑

**唔好**每 ingest 一則新聞/文章就即刻跑 matching。Matching 想食盡成個
`company_fact_embeddings` corpus，遲少少、夾埋一批新聞一齊跑先啱：

```bash
python -m app.cli run-matching
```

呢個會對 DB 入面**全部**新聞跑一次：

- `match_news_to_companies()` —— Layer 2（tag/category 規則）+ Layer 3（embedding 語義比對），
  補埋 Layer 1（直接提及，ingest 嗰陣已經做咗）漏低嘅公司
- `match_news_to_articles()` —— shared_company / shared_tag / embedding 三層，搵新聞同分析
  文章之間嘅關聯

兩個 matcher 都係 idempotent（淨係補未覆蓋嘅 match，唔會重複插入），所以隨時可以重跑。

**幾時要跑**：
- 用戶明確講「跑吓 matching」/「幫我 match 埋」
- 用戶一次過貼咗好幾則新聞/文章之後，問「有冇邊啲新聞同邊間公司/邊篇文章有關」
- 生產環境應該用 cron 定時觸發（例如每 15-60 分鐘一次），同 ingestion 分開唔同 cadence——
  詳細原因見 `app/etl/run_matching.py` 檔案頭註解

## 要留意嘅嘢

- **成本**：每次 ingest 都會 call 幾次 DeepSeek（LLM 分析）+ 如果有新公司就仲會加埋
  yfinance/SEC lookup + 3 次 LLM call；成功之後仲有一次 OpenAI embedding call。用戶一次過
  貼十幾廿則嘢想全部 ingest 嗰陣，先同用戶確認一聲先大量執行，唔好靜雞雞爆佢啲 API quota。
- **環境**：`.env` 要有 `DATABASE_URL`（Supabase）、`OPENAI_API_KEY`（其實係 DeepSeek key，
  歷史命名問題）、`EMBEDDING_API_KEY`（真正 OpenAI key，畀 embedding 用）三個都設定好。
- **執行位置**：一定要喺 `stock_news_db/` 呢層，`python -m app.cli ...`，唔係
  `python app/cli.py ...`（relative import 會炸）。
- 想睇單一 news_id 嘅 matching 結果，可以直接查 `news_company_link` / `news_article_matches`
  兩張 table，唔使再跑 code。
