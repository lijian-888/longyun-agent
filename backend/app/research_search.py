"""Privacy-aware Tavily search for current public research references."""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# Only these generic research topics are allowed to leave the local platform
# automatically. Raw user questions, private attachment text, variety records,
# and numerical trial data are never sent to Tavily.
PUBLIC_TOPIC_TERMS = (
    "水稻", "稻米", "育种", "表型", "根系", "种质", "基因", "qtl", "基因组",
    "稻瘟病", "白叶枯病", "纹枯病", "病虫害", "抗病", "抗倒伏", "耐盐", "耐旱",
    "栽培", "施肥", "植保", "农业", "小麦", "玉米", "大豆", "论文", "文献", "研究进展",
)
SEARCH_TRIGGERS = ("最新", "近期", "近年", "论文", "文献", "研究进展", "研究成果", "公开资料", "公开信息", "检索", "搜索")
TRUSTED_DOMAIN_SUFFIXES = (
    ".gov.cn", ".edu.cn", ".ac.cn", ".gov", ".edu", ".ac.uk",
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "doi.org", "nature.com",
    "springer.com", "sciencedirect.com", "wiley.com", "frontiersin.org",
    "mdpi.com", "cell.com", "pnas.org", "science.org", "cnki.net",
    "wanfangdata.com.cn", "sciengine.com", "cabi.org",
)


@dataclass(frozen=True)
class PublicSearchResult:
    title: str
    url: str
    snippet: str
    source_kind: str


def needs_current_public_search(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in SEARCH_TRIGGERS)


def _requested_domains(question: str) -> list[str]:
    domains: list[str] = []
    for url in re.findall(r"https?://[^\s<>]+", question):
        hostname = (urlparse(url).hostname or "").lower()
        if hostname and hostname not in domains:
            domains.append(hostname)
    return domains[:3]


def build_safe_public_query(question: str) -> tuple[str | None, list[str]]:
    """Reduce a user request to generic public topic terms.

    The conservative allow-list prevents trial numbers, private variety names,
    attachment wording, and other user-provided research facts from becoming a
    third-party search query.
    """
    lowered = question.lower()
    topics = [term for term in PUBLIC_TOPIC_TERMS if term in lowered]
    if not topics:
        return None, _requested_domains(question)
    unique_topics = list(dict.fromkeys(topics))[:6]
    query = " ".join(unique_topics + ["最新研究论文"])
    return query, _requested_domains(question)


def _trusted(url: str, requested_domains: list[str]) -> tuple[bool, str]:
    hostname = (urlparse(url).hostname or "").lower()
    if any(hostname == domain or hostname.endswith(f".{domain}") for domain in requested_domains):
        return True, "user_specified_site"
    if any(hostname == suffix or hostname.endswith(suffix) for suffix in TRUSTED_DOMAIN_SUFFIXES):
        return True, "trusted_public_source"
    return False, "unverified_public_source"


async def search_public_references(question: str) -> tuple[list[PublicSearchResult], str | None]:
    """Return trusted public snippets for the current answer only.

    Callers may retain source cards with the conversation as answer evidence,
    but results are never ingested into the platform knowledge base or the
    published-standard-data tables.
    """
    if not needs_current_public_search(question):
        return [], None
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return [], "未配置 Tavily API Key，本次未检索公开论文或近期资料。"
    query, requested_domains = build_safe_public_query(question)
    if not query:
        return [], "为避免将可能的私有研究内容发送到外部搜索服务，未能生成安全的公开检索主题。"
    payload = {
        "query": query,
        "topic": "general",
        "search_depth": "basic",
        "chunks_per_source": 2,
        "max_results": 8,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_domains": requested_domains,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                TAVILY_SEARCH_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
            raw_results = response.json().get("results", [])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return [], "Tavily API Key 无效，本次未使用公开搜索结果。"
        if exc.response.status_code == 403:
            return [], "Tavily 拒绝了本次检索请求，请检查 Key 权限与套餐能力。"
        if exc.response.status_code in {429, 432, 433}:
            return [], "Tavily 搜索额度或频率受限，本次未使用公开搜索结果。"
        return [], "公开搜索服务暂时不可用，本次未使用公开搜索结果。"
    except httpx.HTTPError:
        return [], "公开搜索服务网络不可用，本次未使用公开搜索结果。"

    results: list[PublicSearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "")
        trusted, source_kind = _trusted(url, requested_domains)
        if not trusted:
            continue
        title = str(item.get("title") or url).strip()
        snippet = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:1200]
        if url and title and snippet:
            results.append(PublicSearchResult(title=title, url=url, snippet=snippet, source_kind=source_kind))
    if not results:
        return [], "未找到可作为证据展示的可信公开来源。"
    return results[:5], None
