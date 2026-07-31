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
from app.etl.clean_news import classify_news_type, match_companies, normalize_article
from app.etl.embed_company_facts import embed_article, embed_news
from app.etl.fetch_news import fetch_raw_news
from app.etl.load_news import load_article
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



def build_company_lookup(session) -> dict[str, int]:
    companies = session.query(Company).all()
    lookup: dict[str, int] = {}
    for c in companies:
        lookup[c.ticker] = c.company_id
        lookup[c.name_en] = c.company_id
    return lookup


def run() -> None:
    raw_articles = fetch_raw_news()
    print(f"攞到 {len(raw_articles)} 則原始新聞")

    with get_session() as session:
        company_lookup = build_company_lookup(session)

        for raw in raw_articles:
            article = normalize_article(raw)
            if not article["title"] or not article["published_at"]:
                continue  # 跳過資料不完整嘅新聞

            matched = match_companies(article, company_lookup)
            news_type = classify_news_type(article, matched)
            load_article(session, article, news_type, matched)

        session.commit()

    print("今日新聞已經寫入 DB。")


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
            published_at=published_at or datetime.now(timezone.utc),
            news_type=analysis.get("news_type", "company"),
            sentiment=analysis.get("sentiment"),
            company_ids=company_ids,
            tag_names=_as_list(analysis.get("tags")),
        )
        _embed_after_save(embed_news, news.news_id, db=db)
        return news
    elif type_of_analysis == "article":
        article = db.add_analysis_article(
            title=analysis["title"],
            description=analysis.get("description"),
            content=text,
            source=source,
            url=url,
            published_at=published_at or datetime.now(timezone.utc),
            thesis=analysis.get("thesis"),
            conclusion=analysis.get("conclusion"),
            sentiment=analysis.get("sentiment"),
            company_ids=company_ids,
            tag_names=_as_list(analysis.get("tags")),
        )
        # article 底層都係一行 news，兩邊都要 embed：news.description 一份、article.thesis 一份。
        _embed_after_save(embed_news, article.news_id, db=db)
        _embed_after_save(embed_article, article.news_id, db=db)
        return article




if __name__ == "__main__":
    analysis_content = """Alibaba (BABA) has finally delivered some good numbers in the recent earnings despite worries about the impact of chip restrictions and tariffs. Alibaba Cloud reported 26% YoY revenue growth, while the international commerce business reported 19% YoY growth. The China e-commerce business also picked up some momentum with 10% YoY growth. Both Alibaba Cloud and the international commerce segment showed a good EBITA trend, while there was a 21% dip in the EBITA of the China e-commerce segment. The most important factor in favor of Alibaba is that these results were delivered while many analysts cautioned about the geopolitical tensions. In my previous article, it was mentioned that the tariff war could lead to more fiscal spending in China, which might help Alibaba.

Alibaba's Qwen3 AI model is already on the leaderboard and is often listed among the top ten AI models. The recent news about the launch of a new AI chip by Alibaba should help the company build chips that are ideal for its operations instead of relying on Nvidia (NVDA) or other AI chip makers. The recent ban by China on tech companies from buying Nvidia chips will be a big boost for Alibaba's AI chip development effort. According to a recent CCTV broadcast, Alibaba's AI chips are now giving a performance that matches Nvidia's H20 chips, which were allowed in China. This should help improve the growth trajectory of Alibaba Cloud. According to a Canalys report, Alibaba Cloud has 33% market share in China's cloud infrastructure services market, giving it the market leadership position.

For the current fiscal year, Alibaba is expected to show EPS of $7.86, which gives the stock a forward PE of 20. However, there is a big gap between low and high EPS estimates, which reflects the uncertainty regarding the EPS trajectory in the next few quarters. It is likely that we will see a higher revenue and EBITA share from Alibaba Cloud and international operations, which should provide a strong tailwind for the company and improve the sentiment around the stock.

Alibaba Cloud's Growth Potential is Underestimated
In my previous article, it was mentioned that strong double-digit growth in Alibaba Cloud could improve the stock momentum. We have seen this in the current earnings. Despite missing the EPS and revenue estimates, Alibaba stock has surged by 40% in the last month due to better YoY growth numbers in Alibaba Cloud and news about AI chips and models being priced in.

Recent earnings numbers of Alibaba.
Recent earnings numbers of Alibaba (Seeking Alpha)

Alibaba Cloud reported revenue of $4.6 billion in the current fiscal year. However, this is still quite modest revenue when we compare it to the revenue numbers of the Big Three cloud companies in the U.S., which include Amazon's (AMZN) AWS, Microsoft's (MSFT) Azure, and Google Cloud (GOOG). The cloud business penetration in China is quite low compared to the overall economic size. As an example, Google Cloud is in the third position among cloud players in the U.S. and was still able to deliver revenue of $13.6 billion in the recent quarter. This is three times that of Alibaba Cloud.

Recent numbers by Alibaba.
Recent numbers by Alibaba (Company Filings)

As the overall cloud market increases in China, we could see a faster growth trajectory in Alibaba Cloud. It is also trying to increase its presence in international locations, especially in Southeast Asia. It recently announced new data centers in Malaysia and the Philippines.

Alibaba's Qwen3 model is beating DeepSeek on AI leaderboard.
Alibaba's Qwen3 model is beating DeepSeek on the AI leaderboard (Artificial Analysis)

A big tailwind for Alibaba Cloud is the competitive AI models launched by the company. Alibaba's Qwen3 model is now beating the popular DeepSeek among AI models in China. Alibaba has more resources to deploy these AI tools and services for customers compared to many other cloud players in China. This puts Alibaba in a better position to maintain and improve its market leadership position in terms of AI models in the next few iterations.

Ban on Nvidia Chips and New Customers for Alibaba AI Chips
China has recently banned the use of Nvidia's chips for technology companies operating within China. There was already a big push for domestic AI chip development. There was an earlier broadcast by CCTV showing that Alibaba's AI chips are giving comparable performance to Nvidia's H20 chips. We need to wait for independent benchmarks to show the relative performance of Alibaba's AI chips. But this broadcast shows that Alibaba's AI chip development is also strongly supported by the regulators. According to Reuters, Alibaba's chip unit, T-Head, supplied 72% of the 23,000 AI chips used in building a recent $390 million data center by China Unicom.

Chart
Data by YCharts
Alibaba's stock has already built good momentum in the last few weeks as news about these AI chips was released. We can compare the 40% jump in Alibaba's stock price with the stagnant price in Nvidia over the last month and see the impact of these new AI regulations in China. Despite the recent bull run in Alibaba stock, the valuation multiple of Alibaba is still significantly lower than Nvidia, as mentioned below. This can give investors looking for an AI bet a better entry point.

Comparison of Nvidia's H20 chips with Alibaba's.
Comparison of Nvidia's H20 chips with Alibaba's (CCTV, SCMP)

According to a recent Canalys report, Alibaba Cloud has a big lead in China's cloud infrastructure market. The Big Three in China's cloud market have a market share of 61%, which is lower than the Big Three players in the U.S. It is likely that as the cloud market matures in China, the bigger players will corner a higher market share. This can help Alibaba Cloud deliver a strong YoY growth trajectory in the next few years and also improve the EBITA margin, which currently stands at only 9%, compared to the close to 30% operating margin of Amazon's AWS.

Economies of scale help improve the margins rapidly within the cloud business. We have seen this with Google Cloud, which reported a 20% operating margin in the recent quarter compared to 10% in the year-ago quarter and a negative margin a few years back.

Rapid operating margin improvement in Google Cloud.
Rapid operating margin improvement in Google Cloud (Google Filing)

Besides economies of scale, Alibaba has an added advantage due to the recent ban on Nvidia's chips in China. This should allow the company to use its own AI chips and AI models within Alibaba Cloud, which can increase customer loyalty and also give Alibaba better pricing power in this segment. Alibaba has already cornered 33% of the cloud infrastructure segment in China, according to a recent Canalys report.

Market share of cloud players in China.
Market share of cloud players in China (Canalys)

International Operations Move Towards Profitability
Alibaba has been investing heavily in international operations to increase the growth runway and provide better geographical diversification for the company. In the recent quarter, the international commerce segment reported YoY growth of 19%. There has also been a big reduction in EBITA losses in this segment. In the year-ago quarter, the international commerce segment reported losses of RMB 3.7 billion. This has decreased to only RMB 57 million, or $8 million of losses in the current quarter.

EBITA numbers of Alibaba.
EBITA numbers of Alibaba (Company Filings)

It should be noted that Alibaba faces intense competition in many international locations, particularly from TikTok Shop. Under this pressure, Alibaba's international commerce group has been able to reduce losses by over half a billion dollars in the recent quarter compared to the year-ago quarter. The management seems to be investing more in AI and Alibaba Cloud infrastructure while reducing losses in the international commerce segment.

Mixed Bag in Alibaba's China E-commerce Group
Alibaba's China e-commerce group has started showing good numbers in terms of revenue growth. In the recent quarter, the YoY revenue growth was 10% in this segment. This is much higher than the low single-digit numbers in the last few quarters. However, on the negative side, the EBITA from this segment dropped by 21% to $5.3 billion. The China e-commerce group is still the main contributor for EBITA, and any drop in this segment leads to a big dip in consolidated EBITA. In the current quarter, the consolidated EBITA dropped by 14% despite improvement in Alibaba Cloud and the international commerce segment.

The local rivals continue to put pressure on the pricing ability of Alibaba by giving big discounts to customers. Some of the EBITA dip in the China e-commerce business could also be due to tariffs, which have had a negative impact on the supply chain for many items in China. A bigger-than-expected decline in EBITA of this important segment can hurt the cash flow of the company. This would also limit the ability of Alibaba to invest aggressively in new AI tools and services.

An Attractive Price Among AI Companies
There has been growing talk of an AI bubble in recent months. The valuation of many AI companies has become too expensive. Investors looking for an AI investment can look at Alibaba as a possible option. Alibaba stock offers modest valuation where its forward PE is 20.7 for the fiscal year ending March 2026 and only 16 for the fiscal year ending March 2027. If Alibaba comes out as the main leader in the AI race within China, Wall Street could significantly improve the valuation multiple for the company.

The consensus EPS estimate for the current fiscal year is $7.86, which is a decline of 13.39% from a year ago. At this EPS, the stock is trading at a multiple of 20.7. However, there is a big gap between the low and high EPS estimates for the current fiscal year. The lowest EPS estimate is $6.5, while the highest is $10, which is 50% higher than the low EPS estimate. A big reason behind this gap is the uncertainty of tariffs. However, the fundamentals are still very good, and Alibaba stock could certainly gain a higher multiple as the importance of AI services and Alibaba Cloud increases.

EPS projection of Alibaba
EPS projection of Alibaba (Seeking Alpha)

Chart
Data by YCharts
Alibaba's diversified business along with the cloud segment can be compared with Amazon, while the new AI chips built by Alibaba are replacing Nvidia's chips in China, according to recent reports. Alibaba's forward PE of 20.7 is still significantly lower than Nvidia's forward PE of 40 and Amazon's forward PE of 35. As mentioned earlier, Alibaba supplied 72% of the AI chips that were powering a big data center for China Unicom. This shows the customer demand for Alibaba's AI chips within China. We could also see better iterations of Alibaba's AI chips in the next few quarters.

Alibaba's stock could be at an inflection point as new AI chips are launched and regulators support the development of domestic AI tech.

Investor Takeaway
Alibaba Cloud has reported 26% YoY revenue growth. The EBITA has increased to $412 million in the recent quarter, or $1.6 billion on an annualized basis. Alibaba Cloud is the market leader in China and is launching new AI models and tools rapidly. The new AI chips can also help improve the brand position of its cloud services. However, China's cloud market is still quite small compared with the U.S. when we look at the relative GDP size of the two countries. This gives a longer growth runway for Alibaba Cloud. Rapid international expansion will also improve the growth potential of Alibaba Cloud.

There has been a big reduction in losses in international e-commerce business, which can help the management divert more resources to its AI initiatives. At the same time, it will be important to gauge the future EBITA direction of Alibaba's China e-commerce business, which contributes the biggest share of cash flow to the company. Alibaba's valuation is still very modest, and it does not reflect the rapid improvement in its AI chips and services. This makes the stock quite attractive at the current price."""

    try:
        result = analyze_and_save("article", analysis_content, source="Example Source", url="https://example.com/news/nvidia-investment", published_at=datetime.now(timezone.utc))
        logger.info("新聞已經分析同存入 DB: %s", result)
    except Exception as e:
        logger.error("分析新聞時出錯: %s", e)
    