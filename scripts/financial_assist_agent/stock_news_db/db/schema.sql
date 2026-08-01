-- 純 SQL 版本嘅 schema，方便你直接喺 psql / Supabase SQL editor 對照睇，
-- 或者唔想用 Alembic 嗰陣直接 run 呢個檔案做初始化。
-- 正式開發建議用 Alembic migrations（migrations/ 目錄）嚟管理版本。

CREATE TABLE sectors (
    sector_id        SERIAL PRIMARY KEY,
    sector_name      VARCHAR(100) NOT NULL,
    parent_sector_id INTEGER REFERENCES sectors(sector_id)
);

CREATE TABLE companies (
    company_id    SERIAL PRIMARY KEY,
    ticker        VARCHAR(20) UNIQUE NOT NULL,
    name_en       VARCHAR(255) NOT NULL,
    name_zh       VARCHAR(255),
    exchange      VARCHAR(50),
    country       VARCHAR(100),
    sector_id     INTEGER REFERENCES sectors(sector_id),
    listing_date  DATE,
    status        VARCHAR(20) NOT NULL DEFAULT 'active',
    website       VARCHAR(255),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_companies_ticker ON companies(ticker);

CREATE TABLE company_profiles (
    profile_id      SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    business_model  TEXT,
    description     TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    effective_date  DATE,
    is_current      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE products (
    product_id    SERIAL PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(company_id),
    product_name  VARCHAR(255) NOT NULL,
    category      VARCHAR(100),
    description   TEXT
);

CREATE TABLE tags (
    tag_id    SERIAL PRIMARY KEY,
    tag_name  VARCHAR(100) UNIQUE NOT NULL,
    tag_type  VARCHAR(50) NOT NULL  -- macro / asset_class / theme / industry
);

CREATE TABLE news (
    news_id      SERIAL PRIMARY KEY,
    title        VARCHAR(500) NOT NULL,
    content      TEXT,
    source       VARCHAR(255),
    url          VARCHAR(1000),
    published_at TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    news_type    VARCHAR(30) NOT NULL DEFAULT 'company', -- company / industry / macro / other_asset
    sentiment    VARCHAR(20)                              -- positive / negative / neutral
);
CREATE INDEX idx_news_published_at ON news(published_at);

-- 全文搜尋 index（PostgreSQL 內建），方便日後做關鍵字搜尋
CREATE INDEX idx_news_content_fts ON news USING GIN (to_tsvector('english', coalesce(content, '')));

CREATE TABLE news_company_link (
    news_id     INTEGER NOT NULL REFERENCES news(news_id),
    company_id  INTEGER NOT NULL REFERENCES companies(company_id),
    relevance   REAL,  -- 0-1，呢則新聞對呢間公司嘅相關度
    PRIMARY KEY (news_id, company_id)
);

CREATE TABLE news_tag_link (
    news_id INTEGER NOT NULL REFERENCES news(news_id),
    tag_id  INTEGER NOT NULL REFERENCES tags(tag_id),
    PRIMARY KEY (news_id, tag_id)
);

-- 資料量大咗之後可以考慮:
-- 1. 將 news 表按 published_at 做 monthly partition
-- 2. news_company_link.company_id / news_tag_link.tag_id 加 index 加快反查
CREATE INDEX idx_news_company_link_company ON news_company_link(company_id);
CREATE INDEX idx_news_tag_link_tag ON news_tag_link(tag_id);
