# Stock News DB

股票市場公司背景 + 每日市場新聞資料庫。

Stack：PostgreSQL（本地用 Docker，日後搬去 Supabase）+ Python（SQLAlchemy + Alembic + ETL script）。

## 目錄結構

```
stock-news-db/
├── docker-compose.yml     # 本地起 PostgreSQL（版本對齊 Supabase）
├── requirements.txt
├── .env.example           # 複製做 .env，填返自己嘅設定
├── alembic.ini             # Alembic migration 設定
├── db/
│   └── schema.sql         # 純 SQL 版本 schema（對照參考用）
├── migrations/             # Alembic migration 檔案（版本控制 schema 變更）
│   ├── env.py
│   └── versions/
├── app/
│   ├── config.py           # 讀取環境變數 (.env)
│   ├── database.py         # DB engine / session
│   ├── models/              # SQLAlchemy models（即係你嘅 schema）
│   │   ├── company.py      # Sector, Company, CompanyProfile, Product
│   │   ├── tag.py           # Tag
│   │   └── news.py         # News, NewsCompanyLink, NewsTagLink
│   └── etl/                 # 每日新聞 pipeline
│       ├── fetch_news.py   # 攞原始新聞（API / RSS）
│       ├── clean_news.py   # 清理 + 配對公司/分類
│       ├── load_news.py    # 寫入 DB
│       └── run_daily.py    # 排程執行入口
├── scripts/
│   └── seed_data.py        # 塞返啲測試資料
└── tests/
    └── test_connection.py  # 基本連線 + 建表 smoke test
```

## 本地開發步驟

1. 安裝套件：
   ```
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 複製環境變數檔：
   ```
   cp .env.example .env
   ```

3. 起本地 PostgreSQL：
   ```
   docker compose up -d
   ```

4. 執行 migration，建立所有 table：
   ```
   alembic revision --autogenerate -m "init schema"
   alembic upgrade head
   ```

5. （可選）塞啲測試資料：
   ```
   python -m scripts.seed_data
   ```

6. 試跑每日新聞 pipeline（記得先喺 `.env` 填 `NEWS_API_KEY`）：
   ```
   python -m app.etl.run_daily
   ```

7. 跑測試：
   ```
   pytest
   ```

## 日後搬去 Supabase

1. Supabase Dashboard -> Project Settings -> Database -> Connection string，複製條 URL。
2. 更新 `.env` 入面嘅 `DATABASE_URL`（其他 code 完全唔使改）。
3. 跑 `alembic upgrade head`，將 schema 部署去 Supabase（前提係本機 Postgres 版本同 Supabase 一致，
   Extensions 用到嘅都喺 Supabase 開通咗 —— 詳見 db/schema.sql 入面用到嘅 `GIN` 全文搜尋 index）。
4. 如果本地已經有資料想帶埋去：`pg_dump` 匯出 -> `pg_restore` / `psql` 匯入去 Supabase。

## Schema 設計重點

- `companies` / `company_profiles` / `products` / `sectors`：低頻更新嘅公司背景資料，
  `company_profiles` 特登拆開並加咗 `version` / `is_current`，方便日後 Business Model 有重大轉變時保留歷史。
- `news`：每日 Input 嘅高頻資料，`published_at` 落咗 index，內文用咗 PostgreSQL 全文搜尋 index。
- `news_company_link` / `news_tag_link`：多對多關聯表 —— 一則新聞可以連結多間公司，
  亦可以完全唔連公司、淨係掛 tag（大環境 / 其他資產類新聞）。
