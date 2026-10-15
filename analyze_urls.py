# analyze_urls.py
"""analyze_urls.py — SEO_ENGINE (URLs analysis)

Input:
- input/urls.txt  (1 URL per line)
Fallback if missing:
- output/urls_from_serp.txt

Output:
- output/seo_engine_output.xlsx (append/replace sheet urls_analysis)
"""

from __future__ import annotations

import argparse
import sys
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Ensure we can import from project root when running from src/.
_here = Path(__file__).resolve()
for _p in (_here.parent, _here.parent.parent):
    if (_p / "src_utils.py").exists():
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
        break

from src_utils import PROJECT_ROOT, logger, read_txt_lines, write_xlsx_sheets

INPUT_URLS_TXT = PROJECT_ROOT / "input" / "urls.txt"
INPUT_URLS_TXT_FALLBACK = PROJECT_ROOT / "output" / "urls_from_serp.txt"

OUTPUT_XLSX = PROJECT_ROOT / "output" / "seo_engine_output.xlsx"
SHEET_URLS = "urls_analysis"

STRUCTURAL_IMPORTANCE_WEIGHTS = {
    "title": 3,
    "h1": 3,
    "h2": 2,
    "breadcrumb": 2,
    "meta_description": 1,
    "slug": 1,
}

EXPORT_MIN_STRUCTURAL_IMPORTANCE = 3

DROP_TERMS = {
    "accueil",
    "contact",
    "mentions legales",
    "politique de confidentialite",
    "actualites",
    "home",
}

SPLIT_RE = re.compile(r"\s*(\||—|–|-|·|•|»|›|:|/|,)\s*")


def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def valid_keyword(s: str) -> bool:
    if not s or len(s) < 3:
        return False
    if s in DROP_TERMS:
        return False
    if sum(c.isalpha() for c in s) < 3:
        return False
    return True


def ensure_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith("http"):
        return "https://" + u
    return u


def domain_of(u: str) -> str:
    try:
        d = (urlparse(u).netloc or "").lower()
        if d.startswith("www."):
            d = d[4:]
        return d
    except Exception:
        return ""


def page_type(url: str) -> str:
    u = (url or "").lower()
    path = (urlparse(u).path or "").strip("/")
    if not path:
        return "home"
    if any(x in path for x in ["blog", "actualites", "news", "article"]):
        return "blog"
    if any(x in path for x in ["category", "categorie", "tag", "etiquette"]):
        return "listing"
    if any(x in path for x in ["product", "produit", "shop", "boutique"]):
        return "product"
    return "page"


def split_terms(text: str) -> List[str]:
    if not text:
        return []
    parts = SPLIT_RE.split(text)
    out: List[str] = []
    for p in parts:
        p = normalize(p)
        if valid_keyword(p):
            out.append(p)
    return out


@dataclass
class KeywordAgg:
    keyword: str
    structural_importance: int
    sources: Set[str]


def fetch_html(url: str, timeout: int = 25) -> str:
    headers = {"User-Agent": "SEO_ENGINE/1.0 (+local)", "Accept": "text/html,application/xhtml+xml"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_page_signals(html: str, url: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(" ", strip=True)

    h2_list = [h.get_text(" ", strip=True) for h in soup.find_all("h2")][:15]
    h2 = " | ".join(h2_list)

    meta_description = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        meta_description = md.get("content", "")

    breadcrumb = ""
    nav = soup.find("nav", attrs={"aria-label": re.compile("breadcrumb", re.I)})
    if nav:
        breadcrumb = nav.get_text(" ", strip=True)
    else:
        bc = soup.select_one(".breadcrumb, .breadcrumbs, nav.breadcrumb")
        if bc:
            breadcrumb = bc.get_text(" ", strip=True)

    slug = ""
    try:
        slug = urlparse(url).path.strip("/").replace("-", " ")
    except Exception:
        slug = ""

    return {
        "title": title,
        "h1": h1,
        "h2": h2,
        "meta_description": meta_description,
        "breadcrumb": breadcrumb,
        "slug": slug,
    }


def aggregate_keywords(signals: Dict[str, str]) -> Dict[str, KeywordAgg]:
    agg: Dict[str, KeywordAgg] = {}

    def add(keyword: str, source: str, weight: int) -> None:
        if not keyword:
            return
        if keyword not in agg:
            agg[keyword] = KeywordAgg(keyword=keyword, structural_importance=0, sources=set())
        agg[keyword].structural_importance += weight
        agg[keyword].sources.add(source)

    for source, weight in STRUCTURAL_IMPORTANCE_WEIGHTS.items():
        text = signals.get(source, "")
        if not text:
            continue
        if source == "h2":
            candidates: List[str] = []
            for part in text.split("|"):
                candidates.extend(split_terms(part))
        else:
            candidates = split_terms(text)

        for kw in candidates:
            add(kw, source, weight)

    return agg


def compute_structural_importance_total(signals: Dict[str, str]) -> int:
    score = 0
    for source, w in STRUCTURAL_IMPORTANCE_WEIGHTS.items():
        if (signals.get(source) or "").strip():
            score += w
    return score


def load_urls(path: Path) -> List[str]:
    if path.exists():
        urls = [ensure_url(u) for u in read_txt_lines(path)]
        return [u for u in urls if u]
    if INPUT_URLS_TXT_FALLBACK.exists():
        urls = [ensure_url(u) for u in read_txt_lines(INPUT_URLS_TXT_FALLBACK)]
        return [u for u in urls if u]
    raise FileNotFoundError(f"Missing input URLs file: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEO_ENGINE — URLs analysis")
    parser.add_argument("--input", type=str, default=str(INPUT_URLS_TXT), help="Path to urls TXT")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path

    logger.info("=== START analyze_urls.py ===")
    logger.info(f"Input URLs: {input_path}")

    urls = load_urls(input_path)
    if not urls:
        logger.error("No URLs found.")
        return

    rows: List[Dict[str, str]] = []

    for i, url in enumerate(urls, start=1):
        logger.info(f"[{i}/{len(urls)}] {url}")
        dom = domain_of(url)

        try:
            html = fetch_html(url)
            signals = extract_page_signals(html, url)
            agg = aggregate_keywords(signals)

            extracted = [(k, a) for k, a in agg.items() if a.structural_importance >= EXPORT_MIN_STRUCTURAL_IMPORTANCE]
            extracted.sort(key=lambda kv: (-kv[1].structural_importance, kv[0]))

            extracted_keywords = [k for k, _ in extracted][:25]
            top_kw = [f"{k}({a.structural_importance})" for k, a in extracted[:25]]

            rows.append(
                {
                    "url": url,
                    "domain": dom,
                    "page_type": page_type(url),
                    "structural_importance": compute_structural_importance_total(signals),
                    "extracted_keywords": "|".join(extracted_keywords),
                    "extracted_keywords_scored": "|".join(top_kw),
                    "title": signals.get("title", ""),
                    "meta_description": signals.get("meta_description", ""),
                    "h1": signals.get("h1", ""),
                    "status": "ok",
                }
            )

        except Exception as e:
            logger.error(f"Fetch/extract error: {url} -> {e}")
            rows.append(
                {
                    "url": url,
                    "domain": dom,
                    "page_type": page_type(url),
                    "structural_importance": "",
                    "extracted_keywords": "",
                    "extracted_keywords_scored": "",
                    "title": "",
                    "meta_description": "",
                    "h1": "",
                    "status": "fetch_error",
                }
            )

    write_xlsx_sheets(OUTPUT_XLSX, sheets={SHEET_URLS: rows}, mode="append")

    logger.info(f"Updated XLSX: {OUTPUT_XLSX} (sheet={SHEET_URLS}, rows={len(rows)})")
    logger.info("=== END analyze_urls.py ===")


if __name__ == "__main__":
    main()
