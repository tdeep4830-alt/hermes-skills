from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    集中管理環境變數。
    本地開發讀 .env；日後搬去 Supabase，淨係要換 DATABASE_URL，
    唔使改任何 code。
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/stock_news_db"
    )
    NEWS_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    EMBEDDING_API_KEY: str = ""
    APP_ENV: str = "local"


settings = Settings()
