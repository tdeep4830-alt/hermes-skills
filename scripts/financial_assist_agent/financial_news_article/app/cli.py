"""
Stock News DB pipeline 嘅命令行入口，畀 agent/人手用嚟 ingest 新聞/文章、觸發 matching，
唔使逐次自己砌 `python -c "..."` 咁樣傳大段文字入去（容易撞 shell quoting 問題）。

用法：
    cd stock_news_db
    python -m app.cli ingest-news --file news.txt [--source "Reuters"] [--url "https://..."]
    echo "新聞原文" | python -m app.cli ingest-news
    python -m app.cli ingest-article --file article.txt
    python -m app.cli run-matching
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from app.etl.run_daily import analyze_and_save
from app.etl.pipeline import ingest_news, ingest_article, run_matching

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _read_text(file_path: str | None) -> str:
    if file_path:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    text = sys.stdin.read()
    if not text.strip():
        raise SystemExit("冇文字輸入——用 --file 指定檔案，或者將原文 pipe 落 stdin。")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock News DB pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("ingest-news", "ingest-article"):
        p = subparsers.add_parser(name)
        p.add_argument("--file", default=None, help="讀呢個檔案做原文；唔傳就讀 stdin")
        p.add_argument("--source", default=None)
        p.add_argument("--url", default=None)

    subparsers.add_parser("run-matching")

    args = parser.parse_args()

    if args.command == "ingest-news":
        ingest_news(_read_text(args.file), source=args.source, url=args.url)
    elif args.command == "ingest-article":
        ingest_article(_read_text(args.file), source=args.source, url=args.url)
    elif args.command == "run-matching":
        run_matching()


if __name__ == "__main__":
    main()
