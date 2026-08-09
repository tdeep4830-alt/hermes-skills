import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "scripts" / "financial_assist_agent" / "financial_news_article"
sys.path.insert(0, str(APP_ROOT))
