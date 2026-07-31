
import logging
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
import requests
import time
from pathlib import Path
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)

SEC_USER_AGENT = "tdeep4830@gmail.com"

HEADERS = {"User-Agent": SEC_USER_AGENT}

TICKER_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# 10-K標準Item編號（用嚟做section splitting）
TENK_ITEMS: list[str] = ["1", "1A", "1B", "1C", "2", "3", "4",
                        "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
                        "10", "11", "12", "13", "14", "15", "16"]

_MIN_REQUEST_INTERVAL = 0.15  # 保守啲，唔好逼近10 req/s嘅上限

class SECEdgarClient:
    """負責同SEC EDGAR做HTTP交互，包括rate limit控制。"""

    def __init__(self, user_agent: str = SEC_USER_AGENT, cache_dir: str | Path = ".sec_cache"):
        if "your_email" in user_agent or "YourName" in user_agent:
            logger.warning(
                "SEC_USER_AGENT 好似仲未改—— SEC要求User-Agent要包含真實聯絡資訊，"
                "建議格式：'你個名或者公司名 你嘅email'"
            )
        self.headers = {"User-Agent": user_agent}
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self._last_request_time = 0.0
        self._cik_map_cache: dict[str, str] | None = None

    def _throttled_get(self, url: str) -> requests.Response:
        """保證request之間有最少間隔，避免撞SEC嘅rate limit。"""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        resp = requests.get(url, headers=self.headers, timeout=15)
        self._last_request_time = time.monotonic()
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Ticker -> CIK
    # ------------------------------------------------------------------
    def _load_cik_map(self) -> dict[str, str]:
        """
        SEC 提供一個成個市場嘅 ticker -> CIK 對照表，做local cache，
        唔使成日重新下載（呢個file幾個MB，SEC每日先更新一次）。
        """
        if self._cik_map_cache is not None:
            return self._cik_map_cache

        cache_file = self.cache_dir / "company_tickers.json"
        if cache_file.exists():
            import json
            data = json.loads(cache_file.read_text())
        else:
            resp = self._throttled_get(TICKER_CIK_MAP_URL)
            data = resp.json()
            cache_file.write_text(resp.text)

        # data 格式: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        mapping = {
            entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
            for entry in data.values()
        }
        self._cik_map_cache = mapping
        return mapping

    def get_cik(self, ticker: str) -> str | None:
        """由ticker查返10位數嘅CIK（帶前導零）。"""
        mapping = self._load_cik_map()
        cik = mapping.get(ticker.upper())
        if cik is None:
            logger.warning("搵唔到 %s 嘅CIK", ticker)
        return cik

    # ------------------------------------------------------------------
    # 攞filing list / metadata
    # ------------------------------------------------------------------
    def get_latest_10k_metadata(self, ticker: str) -> dict | None:
        """
        攞返最新一份10-K嘅metadata：
        {accession_number, filing_date, primary_document, document_url}
        """
        cik = self.get_cik(ticker)
        if cik is None:
            return None

        url = SUBMISSIONS_URL_TEMPLATE.format(cik10=cik)
        try:
            resp = self._throttled_get(url)
        except requests.HTTPError:
            logger.exception("攞 %s 嘅submissions失敗", ticker)
            return None

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])

        for form, accession, doc, filing_date in zip(
            forms, accession_numbers, primary_docs, filing_dates
        ):
            if form == "10-K":
                accession_no_dashes = accession.replace("-", "")
                cik_no_zeros = str(int(cik))
                document_url = (
                    f"{ARCHIVES_BASE}/{cik_no_zeros}/{accession_no_dashes}/{doc}"
                )
                return {
                    "ticker": ticker.upper(),
                    "cik": cik,
                    "accession_number": accession,
                    "filing_date": filing_date,
                    "primary_document": doc,
                    "document_url": document_url,
                }

        logger.warning("%s 嘅submissions入面搵唔到10-K", ticker)
        return None

    # ------------------------------------------------------------------
    # 下載filing內文
    # ------------------------------------------------------------------
    def download_10k_html(self, ticker: str, save_dir: str | Path = "10k_filings") -> Path | None:
        """下載最新10-K嘅HTML文件，存落local cache（避免重複下載同一份）。"""
        save_dir = Path(save_dir)
        save_dir.mkdir(exist_ok=True)

        metadata = self.get_latest_10k_metadata(ticker)
        if metadata is None:
            return None

        filename = f"{ticker.upper()}_{metadata['filing_date']}_10K.html"
        filepath = save_dir / filename

        if filepath.exists():
            logger.info("%s 已經下載過，用返local cache", ticker)
            return filepath

        try:
            resp = self._throttled_get(metadata["document_url"])
        except requests.HTTPError:
            logger.exception("下載 %s 10-K失敗", ticker)
            return None

        filepath.write_text(resp.text, encoding="utf-8")
        logger.info("已下載 %s 10-K -> %s", ticker, filepath)
        return filepath


# ----------------------------------------------------------------------------
# Section splitting（將10-K拆做唔同Item）
# ----------------------------------------------------------------------------
def html_to_text(html_content: str) -> str:
    """將10-K嘅HTML轉做plain text，方便之後用regex拆section。"""
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(separator="\n")
    # 清走多餘嘅空行
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def split_10k_sections(text: str, items: list[str] = TENK_ITEMS) -> dict[str, str]:
    """
    將10-K內文拆做 {item編號: 內容text} 嘅dict。

    10-K嘅結構有個常見陷阱：文件開頭嘅Table of Contents（目錄）
    都會出現一次晒所有"Item 1. Business" / "Item 1A. Risk Factors"呢啲字眼，
    跟住真正嘅內容先再出現多一次。

    處理方法：對每個item，搵晒佢喺全文入面所有出現嘅位置，
    用「最後一次出現」當做真正嘅section開始（因為TOC通常喺文件最前面），
    然後將啲header按位置排序，逐段切割。
    """
    # 建立regex pattern，match "Item 1." / "ITEM 1A." 呢類header
    # \b確保1唔會match到11，(?!\w)確保1A唔會match到1AB
    item_positions: dict[str, int] = {}
    for item in items:
        pattern = re.compile(
            rf"\bItem\s+{item}\b\.?(?!\w)", re.IGNORECASE
        )
        matches = list(pattern.finditer(text))
        if matches:
            # 用最後一次出現嘅位置，跳過TOC入面嘅假match
            item_positions[item] = matches[-1].start()

    if not item_positions:
        logger.warning("喺文件入面搵唔到任何標準10-K Item header")
        return {}

    # 按位置排序，方便逐段切割
    ordered = sorted(item_positions.items(), key=lambda kv: kv[1])

    sections: dict[str, str] = {}
    for idx, (item, start_pos) in enumerate(ordered):
        end_pos = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(text)
        sections[item] = text[start_pos:end_pos].strip()

    return sections


def extract_section(html_content: str, item) -> str:
    """快捷方法：直接攞返指定 Item 嘅內文。"""
    text = html_to_text(html_content)
    sections = split_10k_sections(text, TENK_ITEMS)
    return sections.get(item, "")

def fetch_company_profile_by_SEC(ticker: str) -> dict | None:
    """
    用SEC Edgar API攞公司基本資料（主要係sector/industry分類）。
    """
    client = SECEdgarClient()
    metadata = client.get_latest_10k_metadata(ticker)
    if metadata is None:
        return None

    # 下載最新10-K HTML
    html_path = client.download_10k_html(ticker)
    if html_path is None:
        return None

    html_content = html_path.read_text(encoding="utf-8")
    business_summary = extract_section(html_content, "1")
    risk_factors = extract_section(html_content, "1A")
    legal_proceedings = extract_section(html_content, "3")

    return {
        "ticker": ticker.upper(),
        "cik": metadata["cik"],
        "filing_date": metadata["filing_date"],
        "business_summary": business_summary,
        "risk_factors": risk_factors,
        "legal_proceedings": legal_proceedings,
        "document_url": metadata["document_url"],
    }

def fetch_company_profile_by_yfinance(ticker: str):
        """
        攞單一 ticker 嘅公司基本層資料。

        yfinance 嘅 `.info` 得返：
        - longBusinessSummary : 一段free-text描述business model/主要產品，未structured
        - sector / industry   : 有structured分類

        主要產品/供應商/客戶呢啲關係性資料，`.info`度冇——
        淨係將 longBusinessSummary 存做 business_model 嘅原始文字，
        之後可以用LLM（Claude）去讀呢段文字，抽取structured嘅main_products，
        或者手動curate（見 relationships table）。
        """
        try:
            info = yf.Ticker(ticker).info
        except Exception:
            logger.exception("攞 %s 公司profile失敗", ticker)
            return None

        if not info:
            logger.warning("%s 嘅 .info 係空，跳過", ticker)
            return None

        return {
            "ticker": ticker.upper(),
            "name": info.get("longName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "business_summary": info.get("longBusinessSummary"),
        }



