from app.etl.run_weekly_digest import weekly_digest_llm_fn
from app.etl.run_evaluation import weekly_evaluation_fn

"""
每日排程執行嘅入口。
本地測試： python -m app.etl.run_weekly
未來部署：可以用 cron / Airflow 每日觸發呢個 script。
"""

if __name__ == "__main__":
    weekly_digest_llm_fn()
    weekly_evaluation_fn()