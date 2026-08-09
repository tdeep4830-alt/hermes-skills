"""
每日排程執行嘅入口。
本地測試： python -m app.etl.run_daily
未來部署：可以用 cron / Airflow 每日觸發呢個 script。
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from app.database import get_session
from app.manager.db_manager import DatabaseManager
from app.etl.embed_company_facts import embed_article, embed_news
from app.etl.LLM_analyze import AI_analyze, _ANALYSIS_NEWS_SYSTEM_PROMPT, _ANALYSIS_COMPANY_SYSTEM_PROMPT,  _ANALYSIS_RISK_SYSTEM_PROMPT, _ANALYZE_LEGAL_PROCEEDINGS_PROMPT, _ANALYSIS_ARTICLE_SYSTEM_PROMPT
from app.etl.fetch_company import fetch_company_profile_by_SEC, fetch_company_profile_by_yfinance
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
from app.models.company import risk_type_enum
import logging

from app.etl.clean_news import clean_and_prepare
from app.etl.fetch_news import fetch_all
from app.etl.load_news import load_news_items

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)




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


def _embed_after_save(embed_fn, entity_id: int, *, db: DatabaseManager) -> None:
    """
    news/article 一插入 DB 就即時 embed。News/AnalysisArticle 呢行本身已經 commit 咗，
    embedding API call 失敗（例如 rate limit）唔應該累個新聞插入睇落好似失敗咗——
    照樣 log 個 warning，之後 embed_company_facts() 個 batch job 會執返漏低嘅。
    """
    try:
        embed_fn(entity_id, db=db)
    except Exception:
        logger.exception("即時 embed %s(%s) 失敗，等下次 embed_company_facts() batch job 補返", embed_fn.__name__, entity_id)


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

        # LLM 呢 7 個欄位本身就係一句「簡短描述」，冇獨立嘅 name——name 欄位淨係用嚟頂住
        # NOT NULL（截到 255 字），真正嘅內容存喺 description（Text，冇長度限制），
        # embedding script 靠 description 先揀到呢啲 row。
        for product_text in _as_list(company_details.get("main_products")):
            session.add(
                Product(company_id=new_company.company_id, product_name=product_text[:255], description=product_text)
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



def analyze_and_save(
    type_of_analysis: str,
    text: str,
    *,
    source: Optional[str] = None,
    url: Optional[str] = None,
    published_at: Optional[datetime] = None,
    model: str = "deepseek-v4-pro",
) -> News:
    """分析新聞原文，再連公司 / tag 一齊存入 DB。"""
    if type_of_analysis == "news":
        prompt = _ANALYSIS_NEWS_SYSTEM_PROMPT
    elif type_of_analysis == "article":
        prompt = _ANALYSIS_ARTICLE_SYSTEM_PROMPT
    else:
        raise ValueError(f"未知嘅 type_of_analysis: {type_of_analysis!r}，要 'news' 或者 'article'")

    db = DatabaseManager()
    analysis = AI_analyze(text, model=model, prompt=prompt)
    effective_published_at = published_at or datetime.now(timezone.utc)

    company_ids = []
    for ticker in _as_list(analysis.get("tickers")):
        ticker = ticker.upper()
        company = db.get_company_by_ticker(ticker)
        if company is None:
            # 冇呢間公司就用返 save_company 攞齊 yfinance/SEC/LLM 資料先存，
            # 唔淨係得個 ticker 頂住個空殼 company。
            save_company(ticker)
            company = db.get_company_by_ticker(ticker)
        if company is not None:
            company_ids.append(company.company_id)

    if type_of_analysis == "news":
        news = db.add_news(
            title=analysis["title"],
            description=analysis.get("description"),
            content=text,
            source=source,
            url=url,
            published_at=effective_published_at,
            news_type=analysis.get("news_type", "company"),
            sentiment=analysis.get("sentiment"),
            company_ids=company_ids,
            tag_names=_as_list(analysis.get("tags")),
        )
        try:
            _embed_after_save(embed_news, news.news_id, db=db)
        except Exception:
            logger.exception("即時 embed_news(%s) 失敗，等下次 embed_company_facts() batch job 補返", news.news_id)

        return news
    
    elif type_of_analysis == "article":
        article = db.add_analysis_article(
            title=analysis["title"],
            description=analysis.get("description"),
            content=text,
            source=source,
            url=url,
            published_at=effective_published_at,
            thesis=analysis.get("thesis"),
            conclusion=analysis.get("conclusion"),
            sentiment=analysis.get("sentiment"),
            company_ids=company_ids,
            tag_names=_as_list(analysis.get("tags")),
        )
        # article 底層都係一行 news，兩邊都要 embed：news.description 一份、article.thesis 一份。
        try:
            _embed_after_save(embed_article, article.news_id, db=db)
        except Exception:

            logger.exception("即時 embed_article(%s) 失敗，等下次 embed_company_facts() batch job 補返", article.news_id)

        return article


def run() -> dict[str, int]:
    db = DatabaseManager()
    try:
        known_companies = [
            {"company_id": c.company_id, "ticker": c.ticker, "name_en": c.name_en}
            for c in db.list_companies(limit=1000)
        ]
        tracked_tickers = [c["ticker"] for c in known_companies]

        logger.info("開始 fetch 新聞(RSS + Hacker News + Finnhub)...")
        raw_items = fetch_all(tracked_tickers=tracked_tickers)
        logger.info("Fetch 完成，一共 %d 條原始新聞", len(raw_items))

        cleaned_items = clean_and_prepare(raw_items, known_companies)
        logger.info(
            "Clean 完成(AI/Tech relevance filter + dedup)，剩返 %d 條(篩走 %d 條)",
            len(cleaned_items),
            len(raw_items) - len(cleaned_items),
        )

        stats = load_news_items(db, cleaned_items)
        logger.info(
            "寫入完成：新增 %d 條，跳過(已存在) %d 條，跳過(資料不完整) %d 條",
            stats["inserted"],
            stats["skipped_existing"],
            stats["skipped_invalid"],
        )
        return stats
    finally:
        db.dispose()


if __name__ == "__main__":
    run()
