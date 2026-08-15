"""
最基本嘅 model/schema 煙囪測試（smoke test）。

呢個 project 依賴緊 PostgreSQL 專屬嘅 column type(pgvector 嘅 VECTOR、
Postgres 嘅 ARRAY)，SQLite 冧唔到,所以呢度改用返 .env 指住嘅真實 PostgreSQL，
但成個 create_all()/drop_all() 過程包喺一個一定會 rollback 嘅 transaction 入面
——所以無論你個 DATABASE_URL 係咪指住你本地開發緊、已經有資料嘅 DB，
呢個 test 跑完都唔會留低任何改動,一定唔會影響你嘅真實/seed 資料。

前提：DATABASE_URL 指住嘅 PostgreSQL 要已經開緊機
(`docker compose up -d`)。唔需要預先跑過 `alembic upgrade head`——
呢個 test 自己會開返 `vector` extension。

執行： pytest tests/test_connection.py
"""
from sqlalchemy import create_engine, text

from app.config import settings
from app.models import Base


def test_models_create_all():
    engine = create_engine(settings.DATABASE_URL)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                result = conn.execute(text("SELECT 1"))
                assert result.scalar() == 1

                # 確認所有 model(包括用到 pgvector VECTOR / Postgres ARRAY 嘅
                # concepts 表)都可以成功喺真實 PostgreSQL 建表。
                # 留意:如果呢個 DB 已經跑過 alembic upgrade head,啲 table
                # 已經存在,create_all() 嘅 checkfirst 會自動 skip 咗創建
                # (唔算失敗),但 drop_all() 依然會真係 drop 咗佢哋——
                # 不過成個過程包喺會 rollback 嘅 transaction 入面,實際
                # 上完全唔會影響返你個真實 DB。喺一個未跑過 migration
                # 嘅全新 DB 度跑呢個 test,就會真係完整創建一次做驗證。
                Base.metadata.create_all(bind=conn)
                Base.metadata.drop_all(bind=conn)
            finally:
                # 無論成功定失敗都一定 rollback,唔會留低任何改動
                trans.rollback()
    finally:
        engine.dispose()
