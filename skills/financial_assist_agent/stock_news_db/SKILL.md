---
name: stock-news-pipeline-ops
description: Use this skill when running, scheduling, or troubleshooting the stock-news-db pipelines — daily AI/Tech news fetch, historical stock price updates, Mind Map (concept graph) extraction from news/articles, the weekly Mind Map digest, or ex-post evaluation of Mind Map theses against actual stock price performance. Consult this before running any `python -m app.etl.*` command in this project, and before deciding what order to run them in or how often to schedule them.
license: Internal project skill — not for redistribution.
---

# Stock News DB — Pipeline Operations Guide

## 呢個project做緊咩

`stock-news-db` 由兩部分組成：(1) 一個結合公司靜態資料 + 每日AI/Tech新聞嘅
database，(2) 一個由新聞/分析文章用LLM抽取出嚟嘅「Mind Map」(concept
relation graph)，記低市場論述（邊個主題會點樣影響邊間公司、幾多獨立
新聞來源印證緊呢個講法），再加埋股價資料做事後配對，等使用者可以驗證
「Mind Map嘅睇好/睇淡論述，事後計落嚟同實際股價表現係咪有關」。

**重要：呢個系統係一個research/分析工具，唔係交易訊號產生器，亦都唔係
投資建議系統。**任何pipeline嘅輸出（尤其係weekly digest同evaluation
report）都唔應該被用嚟直接生成「買/賣」建議或者對大市方向作預測——
weekly digest嘅LLM prompt已經明文規定佢做「敘事者」（描述Mind Map本身
嘅結構變化）而唔係「先知」（預測大市），evaluation pipeline都淨係計
track record嘅純數字。如果你（AI Agent）被要求根據呢啲輸出畀投資建議，
應該婉拒並且提醒使用者呢啲數字唔構成財務建議。

## 執行方式

所有指令透過 Hermes API Bridge（`http://172.16.1.1:5000`）執行。
**Agent 只需用 `curl` 呼叫 API，唔需要、亦唔應該直接行 `docker` 或 `python` 指令。**


## Pipeline 一覽（按依賴順序）

### 1. 每日新聞 fetch（`app/etl/run_daily.py`）

```
curl -s -X POST http://172.16.1.1:5000/run/run-daily
```

- 做咩：由RSS(TechCrunch/Verge/Ars Technica/VentureBeat/MIT Tech
  Review) + Hacker News + Finnhub(免費層，`FINNHUB_API_KEY`冇填就自動
  跳過)攞AI/Tech新聞，做relevance filter + 去重 + 配對已知公司，寫入
  `news`表。**唔會call任何LLM**，唔會更新Mind Map。
- Idempotent：可以日日重覆跑，同一個`news.url`已存在就自動skip，唔會
  插重複行。
- 建議頻率：日日跑一次。
- 冇任何前置依賴（DB migrate咗就得）。
- 成功嘅log會顯示 `inserted` / `skipped_existing` / `skipped_invalid`
  幾個數字。
- 由一則新聞（`process_news_for_concepts(db, news_id)`）或者一篇
  分析文章（`process_article_for_concepts(db, article_id)`）call LLM
  抽取theme/relation，寫入Mind Map。
- **呢一步而家冇一個`python -m`嘅batch script**——`extract_concepts.py`
  淨係提供兩個function，要逐個news_id/article_id自己call。如果你要
  automate呢一步（例如「每日新聞fetch完之後，逐條未處理嘅新聞都抽取一
  次」），你要自己寫一個小loop（例如攞返`db.list_news(...)`未抽取過嘅
  項目，逐條call `process_news_for_concepts`），或者叫負責呢個project
  嘅人加返一個batch script先用得。**唔好假設呢一步已經自動化咗。**
- 每次call都會消耗Anthropic/OpenAI token，唔建議冇限制咁批量狂call。

### 2. 股價更新（`app/etl/run_price_update.py`）

```
curl -s -X POST http://172.16.1.1:5000/run/run-price-update
```

- 做咩：幫DB入面已有嘅每一間公司，用`yfinance`（免費、唔使key）攞歷史
  日線股價，incremental寫入`stock_prices`表——已經有記錄嘅公司淨係攞
  「最新記錄之後」嘅新資料，全新公司先攞返400日歷史。
- Idempotent：可以重覆跑，同一日嘅記錄會update唔會炒重複。
- 建議頻率：日日跑（同news fetch一齊），或者最少喺跑evaluation之前跑
  一次。
- **呢一步係第4步（事後配對）嘅硬性前置依賴**——冇股價資料，evaluation
  嗰邊嘅訊號會全部標做`evaluable=False`，report會顯示
  `evaluated_signals=0` / `skipped_signals`增加，但唔會拋錯。

### 3. 事後配對（`app/etl/run_evaluation.py`）—— ⚠️要第2步先跑過

```
curl -s -X POST http://172.16.1.1:5000/run/run-evaluation
```

- 做咩：將Mind Map入面「主題 -> 公司」嘅relation底下每條evidence（一個
  時間點嘅訊號），同`stock_prices`嘅實際股價序列配對，計算forward
  return / hit rate，寫低一份整體 + 逐間公司嘅track record report。
  **完全唔call任何外部API，淨係讀DB做計算。**
- **前提：第3步（股價更新）要至少跑過一次**，否則所有訊號都評估唔到。
- 冇任何寫入DB嘅副作用——`EvaluationManagerMixin`刻意設計成每次即時
  計算、唔persist結果落DB。預設`__main__`會寫一份JSON去
  `reports/evaluation_latest.json`；如果你想keep低歷史記錄（例如每個
  月一份存底），唔可以淨係跑`python -m app.etl.run_evaluation`（佢每次
  都寫去同一個檔名，會覆蓋舊嘅），要用以下方式自訂 `output_path`：

  ```
  python -c "
  from app.etl.run_evaluation import run
  from datetime import date
  run(output_path=f'reports/evaluation_{date.today().isoformat()}.json')
  "
  ```

-- 建議頻率：每個星期或者每個月跑一次（睇你想幾密睇一次track record，
  唔使日日跑，因為短horizon例如5個交易日內嘅結果通常都仲未夠數據）。

### 4. 每週Mind Map動向摘要（`app/etl/weekly_digest.py`）

```
curl -s -X POST http://172.16.1.1:5000/run/weekly-digest
```

- 做咩：收集`AnalyticsManagerMixin`嗰六類Mind Map結構訊號（加速印證緊
  嘅relation、新興主題、正負分歧、主題廣度、傳導路徑、來源多樣性），
  叫LLM執筆寫一段俾人讀嘅中文摘要，印出嚟。要`ANTHROPIC_API_KEY`。
- **會call真Anthropic API，有token成本**，唔建議日日跑，建議每個星期
  跑一次。
- 輸出淨係印去stdout，冇自動存檔——如果你想keep低歷史，自己redirect
  output（例如 `python -m app.etl.weekly_digest > reports/digest_$(date +%F).txt`）。
- LLM輸出已經內建「敘事者、唔係先知」嘅原則同免責聲明，唔會預測大市
  方向——唔使亦都唔應該叫佢做呢件事。

## 建議排程組合

| Pipeline | 建議頻率 | 依賴 |
|---|---|---|
| `run_daily.py`（新聞fetch） | 日日 | 無 |
| `run_price_update.py`（股價update） | 日日 | 無 |
| concept extraction（手動/自訂loop） | 你自己揀 | 需要`run_daily.py`已寫入嘅新聞 |
| `run_evaluation.py`（事後配對） | 每週/每月 | **必須喺`run_price_update.py`之後** |
| `weekly_digest.py`（動向摘要） | 每週 | 需要Mind Map已經有concept extraction嘅結果 |

## 故障排查

- **連唔到DB / `psycopg2.OperationalError`**：check`DATABASE_URL`岩唔岩，
  Postgres/Supabase有冇喺度。
- **`alembic`話有pending migration**：跑`alembic upgrade head`。
- **`run_evaluation`嘅report全部`evaluated_signals=0`**：99%係未跑過
  `run_price_update.py`，或者呢啲公司仲未有Mind Map relation連住
  company node（即係未做過concept extraction）。
- **`run_price_update`某啲公司`fetched=0`**：yfinance攞唔到（ticker可能
  喺Yahoo Finance搵唔到、或者網絡/rate limit問題），會log warning但唔
  會累事成個pipeline死——單獨查返嗰個ticker啱唔啱。
- **`weekly_digest`/concept extraction 出錯話冇API key**：check
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`有冇填喺`.env`。

## 唔好做嘅嘢

- 唔好將`run_evaluation.py`嘅輸出直接包裝做「買/賣建議」或者「大市預測」
  俾使用者——呢啲數字純粹係track record統計，唔係財務建議。
- 唔好喺冇跑`run_price_update.py`之前就跑`run_evaluation.py`並且假設
  結果有意義。
- 唔好將`weekly_digest.py`嘅prompt改到叫LLM預測大市方向——呢個違反咗
  成個系統嘅設計原則（LLM係敘事者，唔係先知）。
- 唔好狂call concept extraction而唔限制數量——每次call都燒緊真金白銀
  嘅LLM token。