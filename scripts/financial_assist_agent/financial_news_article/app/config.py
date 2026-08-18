from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 實際放喺 repo root(financial_news_article/ 嘅太爺目錄)，用相對 __file__
# 動態計返條路就唔使理呼叫方個 cwd 係邊(直接跑 script、喺 tests/ 入面跑 pytest
# 都得)，亦唔使理成個 project 而家部署喺邊——local 定係 VPS container(
# /opt/hermes/...)都自動啱，唔好改返做 hardcoded absolute path(local 冧晒)。
_REPO_ROOT_ENV = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    """
    集中管理環境變數。
    本地開發讀 .env；日後搬去 Supabase，淨係要換 DATABASE_URL，
    唔使改任何 code。
    """

    model_config = SettingsConfigDict(env_file=_REPO_ROOT_ENV, extra="ignore")

    DATABASE_URL: str

    FINNHUB_API_KEY : str | None = None
    # Match news to articles 嘅預設參數，日後有多啲數據先再校準。
    # Layer 2 嘅預設參數，日後有多啲數據先再校準。
    SHARED_COMPANY_RELEVANCE: float = 0.6
    SHARED_TAG_RELEVANCE: float = 0.5

    # Layer 3 嘅預設參數，同 match_news_companies.py 一樣，日後有多啲數據先再校準。
    EMBEDDING_SIMILARITY_THRESHOLD: float = 0.3
    EMBEDDING_TOP_K: int = 20

    # Match news to companies 嘅預設參數，日後有多啲數據先再校準。
    # Layer 2 嘅固定信心分數（純規則配對，冇分數概念，用一個中等偏高嘅數頂住）。
    TAG_RULE_RELEVANCE: float = 0.6

    # Layer 3 嘅預設參數，都係得閒可以再調嘅超參數。
    EMBEDDING_SIMILARITY_THRESHOLD: float = 0.75
    EMBEDDING_TOP_K: int = 20


settings = Settings()
