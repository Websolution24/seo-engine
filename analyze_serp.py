# analyze_serp.py
"""
analyze_serp.py — SEO_ENGINE (DataForSEO extraction)

Input:  input/keywords.txt (1 keyword per line)
Output (overwrite each run):
- output/serp_raw.json  (LIST; 1 item = 1 keyword)
- output/run_meta.json  (run metadata; separate file)

Optional:
- --write-bridge -> output/urls_from_serp.txt (top10 organic + local pack URLs)

Defaults overridable via env:
- SEO_ENGINE_LANGUAGE_CODE (default: fr)
- SEO_ENGINE_LOCATION_CODE (default: 2250)

Required env:
- DATAFORSEO_LOGIN
- DATAFORSEO_PASSWORD
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

# ─────────────────────────────────────────────
# ENV + LOGS
# ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_env() -> None:
    """Only the release's own .env; never search a parent directory."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)


LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.remove()
logger.add(
    LOG_DIR / "seo_engine.log",
    rotation="1 MB",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
)
logger.add(
    sys.stdout,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
)

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
INPUT_KEYWORDS_TXT = PROJECT_ROOT / "input" / "keywords.txt"
INPUT_KEYWORDS_TXT_FALLBACK = PROJECT_ROOT / "input" / "keywords_input.txt"

OUTPUT_SERP_JSON = PROJECT_ROOT / "output" / "serp_raw.json"
OUTPUT_RUN_META_JSON = PROJECT_ROOT / "output" / "run_meta.json"
OUTPUT_URLS_BRIDGE_TXT = PROJECT_ROOT / "output" / "urls_from_serp.txt"

OUTPUT_SERP_JSON.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_RUN_META_JSON.parent.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# DataForSEO endpoints
# ─────────────────────────────────────────────
DFS_VOLUME_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
DFS_SERP_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_kw(s: str) -> str:
    return " ".join((s or "").strip().split())


def _canon_kw(s: str) -> str:
    """Canonical form for matching keywords across sources (accents/case/punct)."""
    s = _norm_kw(s).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))  # remove accents
    s = re.sub(r"[^\w\s]", " ", s)  # remove punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _safe_domain(url: str) -> str:
    try:
        netloc = (urlparse(url).netloc or "").lower().strip()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"ENV manquante : {name}")
    return val


def read_keywords_txt(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    seen = set()
    out: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        s = _norm_kw(s)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_keywords(path: Path) -> List[str]:
    if path.exists():
        return read_keywords_txt(path)
    if INPUT_KEYWORDS_TXT_FALLBACK.exists():
        return read_keywords_txt(INPUT_KEYWORDS_TXT_FALLBACK)
    raise FileNotFoundError(f"Missing input keywords file: {path}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_txt_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = []
    seen = set()
    for x in lines:
        s = (x or "").strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    path.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")


def get_defaults() -> Dict[str, Any]:
    language_code = os.getenv("SEO_ENGINE_LANGUAGE_CODE", "fr").strip() or "fr"
    location_code = _safe_int(os.getenv("SEO_ENGINE_LOCATION_CODE", "2250")) or 2250
    return {"language_code": language_code, "location_code": location_code}


# ─────────────────────────────────────────────
# DataForSEO calls
# ─────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def dfs_search_volume(keywords: List[str], language_code: str, location_code: int) -> Dict[str, Dict[str, Any]]:
    login = _require_env("DATAFORSEO_LOGIN")
    password = _require_env("DATAFORSEO_PASSWORD")

    payload = [{"keywords": keywords, "language_code": language_code, "location_code": location_code}]
    r = requests.post(DFS_VOLUME_URL, json=payload, auth=(login, password), timeout=45)
    r.raise_for_status()
    data = r.json() or {}

    task = (data.get("tasks") or [{}])[0]
    sc = task.get("status_code")
    sm = task.get("status_message")
    if sc != 20000:
        raise RuntimeError(f"DataForSEO search_volume error: {sc} {sm}")

    res = task.get("result")

    # DFS can return:
    # - result = [ { "items": [...] } ]
    # - result = [ {...}, {...}, ... ]  (your case)
    if isinstance(res, list) and res:
        if isinstance(res[0], dict) and "items" in res[0]:
            items = res[0].get("items") or []
        else:
            items = res
    else:
        items = []

    def _compute_cpc(it: dict) -> Optional[float]:
        cpc = _safe_float(it.get("cpc"))
        if cpc is not None:
            return cpc
        low = _safe_float(it.get("low_top_of_page_bid"))
        high = _safe_float(it.get("high_top_of_page_bid"))
        if low is not None and high is not None:
            return round((low + high) / 2.0, 4)
        return low if low is not None else high

    def _compute_competition(it: dict) -> Optional[float]:
        ci = _safe_float(it.get("competition_index"))
        if ci is not None:
            return ci
        label = (it.get("competition") or "").upper().strip()
        return {"LOW": 33.0, "MEDIUM": 66.0, "HIGH": 100.0}.get(label)

    out: Dict[str, Dict[str, Any]] = {}
    for it in items:
        kw_raw = str(it.get("keyword", "") or "")
        ck = _canon_kw(kw_raw)
        if not ck:
            continue
        out[ck] = {
            "search_volume": _safe_int(it.get("search_volume")),
            "cpc": _compute_cpc(it),
            "competition": _compute_competition(it),
        }

    for kw in keywords:
        out.setdefault(_canon_kw(kw), {"search_volume": None, "cpc": None, "competition": None})

    logger.info(f"[SV] items_count={len(items)} (keywords={len(keywords)})")
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def dfs_serp_advanced(keyword: str, language_code: str, location_code: int, depth: int = 10) -> Dict[str, Any]:
    login = _require_env("DATAFORSEO_LOGIN")
    password = _require_env("DATAFORSEO_PASSWORD")

    payload = [{"keyword": keyword, "language_code": language_code, "location_code": location_code, "depth": depth}]
    r = requests.post(DFS_SERP_URL, json=payload, auth=(login, password), timeout=60)
    r.raise_for_status()
    data = r.json() or {}

    task = (data.get("tasks") or [{}])[0]
    sc = task.get("status_code")
    sm = task.get("status_message")
    if sc != 20000:
        raise RuntimeError(f"DataForSEO serp error: {sc} {sm}")

    results = task.get("result", []) or []
    return results[0] if results else {}


# ─────────────────────────────────────────────
# SERP parsing
# ─────────────────────────────────────────────
def _parse_paa_block(paa_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(paa_item, dict):
        return out

    # Sometimes PAA container has "items"
    container = paa_item.get("items")
    if isinstance(container, list):
        for el in container:
            if not isinstance(el, dict):
                continue
            q = (el.get("title") or el.get("question") or "").strip()
            url = (el.get("url") or "").strip()
            desc = (el.get("description") or el.get("answer") or "").strip()

            # Some formats have expanded_element
            expanded = el.get("expanded_element") or []
            if isinstance(expanded, list) and expanded:
                ee0 = expanded[0]
                if isinstance(ee0, dict):
                    url = (ee0.get("url") or url).strip()
                    desc = (ee0.get("description") or desc).strip()

            if q:
                out.append({"question": q, "answer_snippet": desc[:500], "url": url})
        return out

    # Or the dict itself is one element
    q = (paa_item.get("title") or paa_item.get("question") or "").strip()
    if q:
        out.append(
            {
                "question": q,
                "answer_snippet": (paa_item.get("description") or paa_item.get("answer") or "")[:500],
                "url": (paa_item.get("url") or "").strip(),
            }
        )
    return out


def _append_related_searches(container: Any, out: List[str]) -> None:
    if container is None:
        return
    if isinstance(container, list):
        for x in container:
            if isinstance(x, str):
                s = x.strip()
                if s:
                    out.append(s)
            elif isinstance(x, dict):
                s = (x.get("title") or x.get("query") or x.get("keyword") or "").strip()
                if s:
                    out.append(s)
    elif isinstance(container, dict):
        s = (container.get("title") or container.get("query") or container.get("keyword") or "").strip()
        if s:
            out.append(s)


def _parse_local_pack_items(local_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(local_item, dict):
        return out

    if "items" in local_item and isinstance(local_item.get("items"), list):
        for sub in local_item.get("items") or []:
            if not isinstance(sub, dict):
                continue
            u = (sub.get("url") or sub.get("website") or "").strip()
            out.append(
                {
                    "rank": sub.get("rank_absolute") or sub.get("rank_group") or sub.get("rank") or "",
                    "title": sub.get("title") or "",
                    "description": "",
                    "url": u,
                    "domain": _safe_domain(u),
                    "type": "local_pack",
                    "rating": sub.get("rating") or "",
                    "reviews": sub.get("reviews_count") or "",
                }
            )
        return out

    u = (local_item.get("url") or local_item.get("website") or "").strip()
    out.append(
        {
            "rank": local_item.get("rank_absolute") or local_item.get("rank_group") or local_item.get("rank") or "",
            "title": local_item.get("title") or "",
            "description": local_item.get("description") or "",
            "url": u,
            "domain": _safe_domain(u),
            "type": "local_pack",
            "rating": local_item.get("rating") or "",
            "reviews": local_item.get("reviews") or local_item.get("reviews_count") or "",
        }
    )
    return out


def parse_serp(keyword: str, serp_result: Dict[str, Any]) -> Dict[str, Any]:
    items = serp_result.get("items", []) or []
    item_types = serp_result.get("item_types") or None

    serp_features = set(item_types) if isinstance(item_types, list) and item_types else set()
    organic_results: List[Dict[str, Any]] = []
    local_pack_results: List[Dict[str, Any]] = []
    paa: List[Dict[str, Any]] = []
    related_searches: List[str] = []

    organic_urls: List[str] = []
    local_pack_urls: List[str] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        it_type = (it.get("type") or "").strip()
        if it_type:
            serp_features.add(it_type)

        if it_type == "organic":
            url = (it.get("url") or "").strip()
            organic_urls.append(url)
            organic_results.append(
                {
                    "rank": it.get("rank_absolute") or it.get("position") or "",
                    "rank_absolute": it.get("rank_absolute"),
                    "rank_group": it.get("rank_group"),
                    "title": it.get("title") or "",
                    "description": it.get("description") or "",
                    "url": url,
                    "domain": it.get("domain") or _safe_domain(url),
                    "type": "organic",
                    "breadcrumb": it.get("breadcrumb") or "",
                    "is_featured_snippet": 1 if it.get("is_featured_snippet") else 0,
                }
            )

        elif it_type in {"people_also_ask", "paa"}:
            paa.extend(_parse_paa_block(it))

        elif it_type in {"related_searches", "related_search", "related"}:
            _append_related_searches(it.get("items"), related_searches)

        elif it_type in {"local_pack", "map_pack"}:
            parsed_lp = _parse_local_pack_items(it)
            for lp in parsed_lp:
                u = (lp.get("url") or "").strip()
                if u:
                    local_pack_urls.append(u)
                local_pack_results.append(lp)

    organic_top10_domains: List[str] = []
    for u in organic_urls[:10]:
        d = _safe_domain(u)
        if d:
            organic_top10_domains.append(d)

    overlap_domains: List[str] = []
    organic_set = set(organic_top10_domains)
    for u in local_pack_urls:
        d = _safe_domain(u)
        if d and d in organic_set and d not in overlap_domains:
            overlap_domains.append(d)

    return {
        "keyword": keyword,
        "serp_features": sorted(list(serp_features)),
        "has_local_pack": bool(local_pack_results),
        "local_pack_overlap": len(overlap_domains),
        "local_pack_overlap_domains": overlap_domains,
        "organic_results": organic_results[:10],
        "local_pack_results": local_pack_results[:20],
        "paa": paa[:30],
        "related_searches": related_searches[:30],
    }


def build_bridge_urls(parsed: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for it in parsed.get("organic_results", []) or []:
        u = (it.get("url") or "").strip()
        if u:
            out.append(u)
    for it in parsed.get("local_pack_results", []) or []:
        u = (it.get("url") or "").strip()
        if u:
            out.append(u)
    return out


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="SEO_ENGINE — DataForSEO extraction")
    parser.add_argument("--input", type=str, default=str(INPUT_KEYWORDS_TXT), help="Path to keywords TXT")
    parser.add_argument("--write-bridge", action="store_true", help="Write output/urls_from_serp.txt")
    parser.add_argument("--allow-paid-api", action="store_true", help="Explicitly allow billable DataForSEO API calls")
    args = parser.parse_args()

    if not args.allow_paid_api:
        parser.error("Paid API calls are disabled. Pass --allow-paid-api to explicitly allow DataForSEO billing.")
    load_project_env()

    started_at = iso_now()
    defaults = get_defaults()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path

    logger.info("=== START analyze_serp.py ===")
    logger.info(f"Project root: {PROJECT_ROOT}")
    logger.info(f"Input keywords: {input_path}")
    logger.info(f"Defaults: {defaults}")

    keywords = load_keywords(input_path)
    if not keywords:
        logger.error("No keywords found in input.")
        return

    logger.info(f"Fetching search volume for {len(keywords)} keywords...")
    sv_map = dfs_search_volume(keywords, defaults["language_code"], defaults["location_code"])

    serp_list: List[Dict[str, Any]] = []
    bridge_all: List[str] = []

    for i, kw in enumerate(keywords, start=1):
        logger.info(f"[{i}/{len(keywords)}] SERP advanced: {kw}")

        ck = _canon_kw(kw)
        sv = sv_map.get(ck, {})

        item: Dict[str, Any] = {
            "keyword": kw,
            "language_code": defaults["language_code"],
            "location_code": defaults["location_code"],
            "search_volume": sv.get("search_volume"),
            "cpc": sv.get("cpc"),
            "competition": sv.get("competition"),
            "serp_features": [],
            "has_local_pack": False,
            "local_pack_overlap": 0,
            "local_pack_overlap_domains": [],
            "organic_results": [],
            "local_pack_results": [],
            "paa": [],
            "related_searches": [],
            "status": "ok",
        }

        try:
            serp_result = dfs_serp_advanced(kw, defaults["language_code"], defaults["location_code"], depth=10)
            parsed = parse_serp(kw, serp_result)

            for k in (
                "serp_features",
                "has_local_pack",
                "local_pack_overlap",
                "local_pack_overlap_domains",
                "organic_results",
                "local_pack_results",
                "paa",
                "related_searches",
            ):
                item[k] = parsed.get(k)

            if args.write_bridge:
                bridge_all.extend(build_bridge_urls(parsed))

        except Exception as e:
            logger.error(f"SERP error for '{kw}': {e}")
            item["status"] = "serp_error"

        serp_list.append(item)

    write_json(OUTPUT_SERP_JSON, serp_list)

    finished_at = iso_now()
    run_meta = {
        "run_id": started_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "input_keywords_count": len(keywords),
        "serp_items_count": len(serp_list),
        "overwritten_outputs": True,
        "defaults": defaults,
    }
    write_json(OUTPUT_RUN_META_JSON, run_meta)

    if args.write_bridge:
        write_txt_lines(OUTPUT_URLS_BRIDGE_TXT, bridge_all)

    logger.info(f"JSON written: {OUTPUT_SERP_JSON}")
    logger.info(f"JSON written: {OUTPUT_RUN_META_JSON}")
    if args.write_bridge:
        logger.info(f"TXT written: {OUTPUT_URLS_BRIDGE_TXT}")
    logger.info("=== END analyze_serp.py ===")


if __name__ == "__main__":
    main()
