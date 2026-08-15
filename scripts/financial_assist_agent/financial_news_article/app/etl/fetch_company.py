
import logging
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
import requests
import time
from pathlib import Path
from bs4 import BeautifulSoup
import re
from app.models import (
    Company,
    CompanyProfile,
    Competitor,
    GovernmentalProgramAndRegulation,
    Industry,
    LegalAndRegulatoryIssues,
    ManufacturingProcess,
    Product,
    Risk,
    Sector,
    Service,
    SupplyChainAndLogistics,
    Technology,
    News,

)
from app.database import get_session
from app.etl.LLM_analyze import AI_analyze, _ANALYSIS_COMPANY_SYSTEM_PROMPT,  _ANALYSIS_RISK_SYSTEM_PROMPT, _ANALYZE_LEGAL_PROCEEDINGS_PROMPT
from app.models.company import risk_type_enum, product_category_values

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



def _get_or_create_sector(session, sector_name: str | None) -> int | None:
    if not sector_name:
        return None
    sector = session.query(Sector).filter_by(sector_name=sector_name).first()
    if sector is None:
        sector = Sector(sector_name=sector_name)
        session.add(sector)
        session.flush()
    return sector.sector_id


def _get_or_create_industry(session, industry_name: str | None, sector_id: int | None) -> int | None:
    if not industry_name:
        return None
    industry = session.query(Industry).filter_by(industry_name=industry_name).first()
    if industry is None:
        industry = Industry(industry_name=industry_name, sector_id=sector_id)
        session.add(industry)
        session.flush()
    return industry.industry_id


def _as_list(value) -> list:
    """AI_analyze 對於「一個或多個 object」嘅 prompt，有時會淨係返一個 dict 冇包 array。"""
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def company_search(ticker: str) -> None:
    """攞返公司嘅基本資料同業務背景。"""
    background = []
    background.append(fetch_company_profile_by_yfinance(ticker))
    info_from_SEC = fetch_company_profile_by_SEC(ticker)
    if not info_from_SEC:
        logger.warning("喺SEC搵唔到 %s 嘅公司資料", ticker)
    else:
        company_search_result_from_LLM = AI_analyze(info_from_SEC["business_summary"], model="deepseek-v4-pro", prompt=_ANALYSIS_COMPANY_SYSTEM_PROMPT)
        risk_search_result_from_LLM = AI_analyze(info_from_SEC["risk_factors"], model="deepseek-v4-pro", prompt=_ANALYSIS_RISK_SYSTEM_PROMPT)
        legal_proceedings_result_from_LLM = AI_analyze(info_from_SEC["legal_proceedings"], model="deepseek-v4-pro", prompt=_ANALYZE_LEGAL_PROCEEDINGS_PROMPT)
        background.append({
            "business_summary": company_search_result_from_LLM,
            "risk_factors": risk_search_result_from_LLM,
            "legal_proceedings": legal_proceedings_result_from_LLM,
        })

    return background

def save_company(ticker: str) -> None:
    """攞返公司嘅基本資料同業務背景，然後存入 DB。"""
    ticker = ticker.upper()
    with get_session() as session:
        company = session.query(Company).filter_by(ticker=ticker).first()
        if company is not None:
            logger.info("公司 %s 已經喺 DB 入面，唔需要再存", ticker)
            return

        background = company_search(ticker)
        yf_profile = background[0] if background else None
        if not yf_profile:
            logger.warning("冇搵到 %s 嘅公司資料，唔會存入 DB", ticker)
            return

        # company_search 淨係喺搵到 SEC 10-K 先會 append 埋 LLM 分析結果，
        # 搵唔到就得返 yfinance 嗰個 profile。
        sec_analysis = background[1] if len(background) > 1 else {}
        company_details = sec_analysis.get("business_summary") or {}
        risk_items = _as_list(sec_analysis.get("risk_factors"))
        legal_items = _as_list(sec_analysis.get("legal_proceedings"))

        sector_id = _get_or_create_sector(session, yf_profile.get("sector"))
        industry_id = _get_or_create_industry(session, yf_profile.get("industry"), sector_id)

        new_company = Company(
            ticker=ticker,
            name_en=yf_profile.get("name") or ticker,
            sector_id=sector_id,
            industry_id=industry_id,
        )
        session.add(new_company)
        session.flush()  # 攞返 new_company.company_id，畀底下嘅 child row 用

        business_model = company_details.get("business_model")
        description = yf_profile.get("business_summary")
        if business_model or description:
            session.add(
                CompanyProfile(
                    company_id=new_company.company_id,
                    business_model=business_model,
                    description=description,
                    is_current=True,
                )
            )

        # main_products 而家係 {name, category, description} object 嘅 array(唔再係
        # 純文字 array)——category 一定要對得住 product_category_values 呢個固定
        # 清單先寫得入，等 TagCategoryRule(target_field="product_category") Layer 2
        # 規則配對用得到；LLM 亂咁作嘅值(或者冇填)一律 fallback 做 "Other"，唔好
        # 因為個值唔喺清單度就成粒 row 唔插。
        for product in _as_list(company_details.get("main_products")):
            product_name = (product.get("name") or "")[:255]
            if not product_name:
                continue
            category = product.get("category")
            if category not in product_category_values:
                category = "Other"
            session.add(
                Product(
                    company_id=new_company.company_id,
                    product_name=product_name,
                    category=category,
                    description=product.get("description"),
                )
            )

        for technology_text in _as_list(company_details.get("technology")):
            session.add(
                Technology(
                    company_id=new_company.company_id,
                    technology_name=technology_text[:255],
                    description=technology_text,
                )
            )

        for service_text in _as_list(company_details.get("main_services")):
            session.add(
                Service(company_id=new_company.company_id, service_name=service_text[:255], description=service_text)
            )

        for program_text in _as_list(company_details.get("governmental_programs_and_regulations")):
            session.add(
                GovernmentalProgramAndRegulation(
                    company_id=new_company.company_id,
                    program_name=program_text[:255],
                    description=program_text,
                )
            )

        for process_text in _as_list(company_details.get("Manufucturing_and_Supply_Chain")):
            session.add(
                ManufacturingProcess(
                    company_id=new_company.company_id,
                    process_name=process_text[:255],
                    description=process_text,
                )
            )

        for supply_chain_text in _as_list(company_details.get("supply_chain")):
            session.add(
                SupplyChainAndLogistics(
                    company_id=new_company.company_id,
                    supply_chain_name=supply_chain_text[:255],
                    description=supply_chain_text,
                )
            )

        for competitor_text in _as_list(company_details.get("competitors")):
            session.add(
                Competitor(
                    company_id=new_company.company_id,
                    competitor_name=competitor_text[:255],
                    description=competitor_text,
                )
            )

        for risk in risk_items:
            risk_type = risk.get("risk_type")
            session.add(
                Risk(
                    company_id=new_company.company_id,
                    risk_type=risk_type if risk_type in risk_type_enum else "other",
                    description=risk.get("risk_description"),
                )
            )

        for issue in legal_items:
            issue_type = issue.get("legal_proceeding_type") or "other"
            session.add(
                LegalAndRegulatoryIssues(
                    company_id=new_company.company_id,
                    issue_title=issue_type.title(),
                    description=issue.get("legal_proceeding_description"),
                )
            )

        session.commit()
        logger.info("已經將公司 %s 存入 DB", ticker)

