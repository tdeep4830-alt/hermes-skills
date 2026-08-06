# Hermes Agent — Stock News Pipeline

呢個 folder 專門畀 Hermes Agent 用,係 `../stock_news_db/` 個完整 project 嘅精簡入口。
淨係露出 4 個 function,唔使 Agent 理會底下嘅 model/manager/migration/fetch 呢啲實現細節。

```python
from hermes_agent import ingest_news, ingest_article, run_matching, find_favorable_news
```

DB 用緊 Supabase(`stock_news_db/.env` 嘅 `DATABASE_URL`),冇本地 Postgres,唔使起 Docker。

## 成條 pipeline 做緊咩(背景知識,唔使手動介入)

1. **LLM 分析** —— DeepSeek 由新聞/文章原文抽 title / description / sentiment / tickers / tags
2. **記錄公司** —— 抽到嘅 ticker 如果 DB 未有,自動攞 yfinance + SEC 10-K 資料,再用 LLM 拆做
   business_model / products / technologies / services / risks / legal_issues 等,一次過建檔
3. **記錄 news/article** —— 寫入 `news`(或加埋 `analysis_article`)table,連埋公司/tag link
4. **Embedding** —— 新聞一存入 DB 即刻 embed(OpenAI embedding),存入 `company_fact_embeddings`
5. **Matching** —— 用 tag 規則 + embedding 語義比對,將新聞同公司/其他文章連埋(搵邊間公司對
   呢單新聞有利好/唔利好)

## 4 個 function

### `ingest_news(text, *, source=None, url=None) -> int`

貼一段**事實/事件報導**(業績、產品發布、併購、監管動作……),回傳 `news_id`。

### `ingest_article(text, *, source=None, url=None) -> int`

貼一篇**分析/觀點文章**(有作者自己嘅 thesis + conclusion,例如 Seeking Alpha 風格長文),
回傳 `news_id`(article 底層都係一行 news)。

**點揀 `ingest_news` 定 `ingest_article`**:段文字主要係報導「發生咗咩事」用 `ingest_news`;
有作者自己嘅論點/推論/「我認為呢隻股會點點點」用 `ingest_article`。唔肯定就當 `ingest_news`
(較平嘅路徑),或者問返用戶。

### `run_matching() -> None`

對 DB 入面全部新聞跑一次 News-Company / News-Article matching。**Idempotent**,隨時可以
重跑,唔會產生重複 link。

**幾時跑**:夾埋一批 `ingest_news`/`ingest_article` 之後先跑一次,**唔好**逐次 ingest
完即刻跑 —— matching(尤其 embedding 語義比對)想食盡成個 embedding corpus,批量跑先合理。

### `find_favorable_news(*, start_date=None, end_date=None, min_relevance=0.0, limit_per_company=5) -> dict[str, list[dict]]`

搵返邊啲公司近日有利好(`sentiment='positive'`)新聞,按 ticker 分組、新→舊排序。每條記錄:
`{"news_id", "title", "published_at", "url", "relevance"}`。

**前提**:要先跑咗 `run_matching()`,先會包埋 tag 規則/embedding 搵到嘅公司,唔淨係得
ingest 嗰陣 LLM 直接提及嘅 ticker。

## 建議流程 —「睇下有咩公司對近日新聞有利好消息」

```python
from hermes_agent import ingest_news, ingest_article, run_matching, find_favorable_news

# 1. 逐則貼新聞/文章(用 LLM 判斷係邊種類型)
ingest_news(news_text_1, source="Reuters", url="https://...")
ingest_article(article_text_1, source="Seeking Alpha", url="https://...")
# ... 一批貼晒

# 2. 貼完一批先跑 matching(唔好逐則跑)
run_matching()

# 3. 攞返最近利好新聞,按公司分組
result = find_favorable_news()
```

## 要留意嘅嘢

- **成本**:每次 ingest 都會 call 幾次 DeepSeek(LLM 分析);如果有新公司仲會加埋
  yfinance/SEC lookup + 3 次 LLM call;成功之後仲有一次 OpenAI embedding call。一次過
  ingest 十幾廿則,考慮分批或者先同用戶確認,唔好靜雞雞爆 API quota。
- **環境變數**:`stock_news_db/.env` 要有 `DATABASE_URL`(Supabase)、`OPENAI_API_KEY`
  (實際係 DeepSeek key,歷史命名)、`EMBEDDING_API_KEY`(真正 OpenAI key,畀 embedding
  用)三個都設定好,呢個 folder 本身冇獨立 `.env`。
- **唔好跳過 `run_matching()`**:唔跑就 `find_favorable_news()` 淨係見到 ingest 嗰陣 LLM
  直接提及嘅 ticker,搵唔到靠語義關聯先搵到嘅公司。
- 想睇更底層嘅實現(models/managers/migrations)或者用 CLI 方式操作,睇
  `../stock_news_db/README.md` 同 `../stock_news_db/.claude/skills/stock-news-pipeline/SKILL.md`。
