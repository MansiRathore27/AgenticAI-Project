import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)
TIMEOUT = 12
MAX_PAGE_CHARS = 12000


def is_government_domain(url: str) -> bool:
    """Allow Indian government domains; reject lookalike hosts."""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return (
            host == "gov.in" or host.endswith(".gov.in")
            or host == "nic.in" or host.endswith(".nic.in")
        )
    except Exception:
        return False


def _classify_result(title: str, url: str, snippet: str = "") -> dict:
    title_l = (title or "").lower()
    url_l = (url or "").lower()
    text_l = title_l + " " + (snippet or "").lower()
    generic_terms = (
        "national scholarship portal", "home |", "homepage", "scheme list",
        "schemes portal", "department of", "welcome to", "search schemes"
    )
    is_generic = any(term in title_l for term in generic_terms) and not any(
        term in text_l for term in ("eligibility", "who can apply", "benefits", "scholarship scheme")
    )
    scheme_terms = (
        "scholarship", "yojana", "scheme", "financial assistance", "fellowship",
        "medhavi", "mukhyamantri", "sambal", "benefit", "stipend"
    )
    path_looks_specific = any(x in url_l for x in (
        "/scheme/", "/schemes/", "/scholarship/", "/scholarships/", "/yojana/"
    ))
    looks_related = any(term in text_l for term in scheme_terms)
    # A scheme can be on an .aspx or other legacy URL, so title/snippet also matter.
    is_scheme = (path_looks_specific or any(
        term in title_l for term in (
            "scholarship", "yojana", "fellowship", "medhavi", "mukhyamantri",
            "sambal", "financial assistance"
        )
    )) and not is_generic
    return {
        "is_scheme_page": bool(is_scheme),
        "looks_scheme_related": bool(looks_related or is_scheme),
        "is_generic_portal": bool(is_generic),
    }


def fetch_page_content(url: str) -> dict:
    """Fetch readable HTML text from an allowlisted Indian government URL."""
    if not is_government_domain(url):
        return {"success": False, "url": url, "error": "Blocked non-government URL."}
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        final_url = response.url
        if not is_government_domain(final_url):
            return {
                "success": False, "url": final_url,
                "error": "Blocked redirect outside an Indian government domain."
            }
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "pdf" in content_type or final_url.lower().split("?")[0].endswith(".pdf"):
            return {
                "success": False, "url": final_url,
                "error": "PDF reading is disabled in this version; use the official HTML scheme page if available.",
                "content_type": content_type,
            }
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(" ", strip=True) if h1 else ""
        return {
            "success": bool(text),
            "title": title,
            "content": text[:MAX_PAGE_CHARS],
            "url": final_url,
            "error": "" if text else "No readable text was found on the page.",
            "content_type": content_type,
        }
    except Exception as exc:
        return {"success": False, "url": url, "error": str(exc)[:300]}


def _search_once(query: str, max_results: int):
    rows_out, errors = [], []
    try:
        with DDGS() as ddgs:
            for row in ddgs.text(query, max_results=max_results):
                url = (row.get("href") or row.get("url") or "").strip()
                if not url or not is_government_domain(url):
                    continue
                title = (row.get("title") or "").strip()
                snippet = (row.get("body") or row.get("snippet") or "").strip()
                rows_out.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    **_classify_result(title, url, snippet),
                })
    except Exception as exc:
        errors.append(f"{query}: {str(exc)[:220]}")
    return rows_out, errors


def search_government_schemes(query: str, max_results: int = 8,
                              fetch_pages: bool = True) -> dict:
    """Search official Indian government domains; snippets are leads, not proof."""
    if not query or not query.strip():
        return {"success": False, "query": query, "results": [],
                "errors": ["Empty search query."]}

    # The planner already creates separate central/state/course queries.
    # Keep each tool call small; multiplying every planner query into four searches
    # caused excessive duplicate requests and search-engine timeouts.
    queries = [
        query.strip(),
        f"{query.strip()} site:gov.in OR site:nic.in",
    ]
    found, errors, seen = [], [], set()
    for search_query in queries:
        rows, row_errors = _search_once(search_query, max(2, max_results // 2))
        errors.extend(row_errors)
        for row in rows:
            key = row["url"].rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                found.append(row)

    # Prefer specific scheme pages and promising official detail pages.
    found.sort(key=lambda x: (
        not x.get("is_scheme_page", False),
        x.get("is_generic_portal", False),
        not x.get("looks_scheme_related", False),
    ))

    if fetch_pages:
        # Fetch a small set of likely relevant results to avoid excessive requests.
        candidates = [r for r in found if not r.get("is_generic_portal")]
        candidates += [r for r in found if r.get("is_generic_portal")]
        for row in candidates[:7]:
            fetched = fetch_page_content(row["url"])
            row["page_status"] = "FETCHED" if fetched.get("success") else "FAILED"
            row["page_title"] = fetched.get("title", "") or row.get("title", "")
            row["page_content"] = fetched.get("content", "")
            row["fetch_error"] = fetched.get("error", "")
            # Reclassify using actual page title/text as some government sites use legacy URLs.
            actual_text = row["page_title"] + " " + row["page_content"][:1800]
            # Classification may use title/snippet to identify a lead, but eligibility
            # scoring later checks the fetched page body only.
            classification = _classify_result(
                row.get("title", "") + " " + row["page_title"],
                row["url"],
                row.get("snippet", "") + " " + actual_text,
            )
            for key, value in classification.items():
                row[key] = bool(row.get(key) or value)
            if row.get("is_generic_portal") and row.get("is_scheme_page"):
                row["is_generic_portal"] = False
    else:
        for row in found:
            row["page_status"] = "NOT_FETCHED"
            row["page_title"] = row.get("title", "")
            row["page_content"] = ""

    return {
        "success": bool(found),
        "query": query,
        "results": found[:max_results * 2],
        "errors": errors,
    }
