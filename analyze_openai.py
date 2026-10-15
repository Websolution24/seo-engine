"""
analyze_openai.py — SEO_ENGINE (IA + scoring + XLSX final)

Lit:
- output/serp_raw.json (LIST; 1 item = 1 keyword) généré par analyze_serp.py

Écrit (overwrite à chaque run):
- output/seo_engine_output.xlsx
  - keywords_analysis
  - serp_overview   (long format)
  - urls_analysis   (placeholder vide; rempli par analyze_urls.py)

Notes:
- Appels OpenAI via HTTP (requests)
- response_format JSON strict
"""

from __future__ import annotations

import json
import argparse
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src_utils import logger, get_env, load_project_env, read_json, write_xlsx_sheets

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
INPUT_JSON = BASE_DIR / "output" / "serp_raw.json"
OUTPUT_XLSX = BASE_DIR / "output" / "seo_engine_output.xlsx"

SHEET_KEYWORDS = "keywords_analysis"
SHEET_SERP_OVERVIEW = "serp_overview"
SHEET_URLS = "urls_analysis"

# ─────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def _clamp(n: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, n))


def _safe_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        netloc = (urlparse(url).netloc or "").lower().strip()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def call_openai_json(prompt: str) -> Dict[str, Any]:
    api_key = get_env("OPENAI_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Tu es un analyste SEO strict et factuel. "
                    "Tu réponds UNIQUEMENT en JSON valide, sans texte additionnel."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    r = requests.post(OPENAI_API_URL, headers=headers, json=payload, timeout=75)
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def build_prompt(entry: Dict[str, Any]) -> str:
    # On envoie un contexte SERP “compact” (pas besoin de raw DFS complet)
    organic = entry.get("organic_results") or []
    localp = entry.get("local_pack_results") or []
    paa = entry.get("paa") or []
    related = entry.get("related_searches") or []

    ctx = {
        "keyword": entry.get("keyword"),
        "search_volume": entry.get("search_volume"),
        "cpc": entry.get("cpc"),
        "competition": entry.get("competition"),
        "serp_features": entry.get("serp_features") or [],
        "has_local_pack": entry.get("has_local_pack"),
        "local_pack_overlap": entry.get("local_pack_overlap"),
        "organic_top10": [
            {
                "rank": x.get("rank"),
                "title": x.get("title"),
                "domain": x.get("domain") or _safe_domain(x.get("url", "")),
                "url": x.get("url"),
            }
            for x in organic[:10]
        ],
        "local_pack": [
            {
                "rank": x.get("rank"),
                "title": x.get("title"),
                "domain": x.get("domain") or _safe_domain(x.get("url", "")),
                "url": x.get("url"),
                "rating": x.get("rating"),
                "reviews": x.get("reviews"),
            }
            for x in localp[:10]
        ],
        "paa_questions": [x.get("question") for x in paa[:15] if x.get("question")],
        "related_searches": [str(x) for x in related[:15]],
    }

    return f"""
Analyse le mot-clé et sa SERP.

Objectif:
- Déduire l'intention principale (intent) parmi: informational / commercial / transactional / navigational / local
- Proposer un type de contenu (recommended_content)
- Proposer un angle (recommended_angle)
- Proposer 5 thèmes sémantiques (semantic_themes)

Réponds en JSON strict avec les clés EXACTES:
intent, recommended_content, recommended_angle, semantic_themes

Contexte:
{json.dumps(ctx, ensure_ascii=False)}
""".strip()


def compute_local_intent_score(entry: Dict[str, Any]) -> int:
    # Deterministe, basé sur signaux SERP
    has_lp = bool(entry.get("has_local_pack"))
    overlap = entry.get("local_pack_overlap") or 0
    score = 20
    if has_lp:
        score = 70
    score += int(overlap) * 5
    return int(_clamp(score, 0, 100))


def compute_difficulty_score(entry: Dict[str, Any]) -> int:
    # Experimental heuristic: Google Ads competition is NOT organic SEO difficulty.
    # Keep the legacy calculation; it has not been calibrated against organic rankings.
    comp = entry.get("competition")
    comp_val: Optional[float] = None
    try:
        comp_val = float(comp) if comp is not None else None
    except Exception:
        comp_val = None

    organic = entry.get("organic_results") or []
    top_domains = [(_safe_domain(x.get("url", "")) or (x.get("domain") or "")) for x in organic[:10]]
    top_domains = [d for d in top_domains if d]

    platforms = {
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "wikipedia.org",
        "doctolib.fr",
        "pagesjaunes.fr",
        "linkedin.com",
    }
    plat_hits = sum(1 for d in top_domains if d in platforms)

    if comp_val is None:
        base = 35 + plat_hits * 8
    else:
        base = comp_val + plat_hits * 5

    return int(_clamp(base, 0, 100))


def compute_opportunity_score(entry: Dict[str, Any], difficulty: int, local_intent: int) -> int:
    vol = entry.get("search_volume")
    try:
        v = int(vol) if vol is not None else 0
    except Exception:
        v = 0

    if v <= 0:
        return 0

    # log scale: 0..100k+
    vol_score = (math.log10(v + 1) / math.log10(100000 + 1)) * 100.0
    score = vol_score - (difficulty * 0.55) + (local_intent * 0.15)
    return int(_clamp(score, 0, 100))


def build_serp_overview_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    kw = entry.get("keyword", "")

    # organic
    for x in (entry.get("organic_results") or [])[:10]:
        rows.append(
            {
                "keyword": kw,
                "block": "organic",
                "rank": x.get("rank"),
                "url": x.get("url"),
                "domain": x.get("domain") or _safe_domain(x.get("url", "")),
                "title": x.get("title"),
                "snippet": x.get("description"),
                "extra": "featured_snippet=1" if x.get("is_featured_snippet") else "",
            }
        )

    # local pack
    for x in (entry.get("local_pack_results") or [])[:20]:
        rows.append(
            {
                "keyword": kw,
                "block": "local_pack",
                "rank": x.get("rank"),
                "url": x.get("url"),
                "domain": x.get("domain") or _safe_domain(x.get("url", "")),
                "title": x.get("title"),
                "snippet": "",
                "extra": f"rating={x.get('rating','')};reviews={x.get('reviews','')}",
            }
        )

    # PAA
    for i, x in enumerate((entry.get("paa") or [])[:30], start=1):
        rows.append(
            {
                "keyword": kw,
                "block": "paa",
                "rank": i,
                "url": x.get("url", ""),
                "domain": _safe_domain(x.get("url", "")),
                "title": x.get("question"),
                "snippet": x.get("answer_snippet", ""),
                "extra": "",
            }
        )

    # related searches
    for i, q in enumerate((entry.get("related_searches") or [])[:30], start=1):
        rows.append(
            {
                "keyword": kw,
                "block": "related_search",
                "rank": i,
                "url": "",
                "domain": "",
                "title": str(q),
                "snippet": "",
                "extra": "",
            }
        )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="SEO_ENGINE — optional OpenAI analysis")
    parser.add_argument("--allow-paid-api", action="store_true", help="Explicitly allow billable OpenAI API calls")
    args = parser.parse_args()
    if not args.allow_paid_api:
        parser.error("Paid API calls are disabled. Pass --allow-paid-api to explicitly allow OpenAI billing.")
    load_project_env()
    global OPENAI_MODEL
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    logger.info("=== START analyze_openai.py ===")

    serp_data: List[Dict[str, Any]] = read_json(INPUT_JSON)
    if not serp_data:
        logger.error("serp_raw.json vide ou absent. Lance analyze_serp.py avant.")
        return

    keywords_rows: List[Dict[str, Any]] = []
    serp_overview_rows: List[Dict[str, Any]] = []

    for i, entry in enumerate(serp_data, start=1):
        kw = entry.get("keyword", "")
        logger.info(f"[{i}/{len(serp_data)}] OpenAI: {kw}")

        # Base data
        serp_features = entry.get("serp_features") or []
        organic = entry.get("organic_results") or []
        top_domains = []
        for x in organic[:10]:
            d = x.get("domain") or _safe_domain(x.get("url", ""))
            if d and d not in top_domains:
                top_domains.append(d)

        paa_q = [x.get("question") for x in (entry.get("paa") or []) if x.get("question")]
        rel = [str(x) for x in (entry.get("related_searches") or [])]

        row: Dict[str, Any] = {
            "keyword": kw,
            "search_volume": entry.get("search_volume"),
            "cpc": entry.get("cpc"),
            "competition": entry.get("competition"),
            "intent": "",
            "experimental_difficulty_proxy": 0,
            "local_intent_score": 0,
            "experimental_opportunity_proxy": 0,
            "serp_features": "|".join([str(x) for x in serp_features]),
            "has_local_pack": 1 if entry.get("has_local_pack") else 0,
            "local_pack_overlap": entry.get("local_pack_overlap", 0),
            "local_pack_overlap_domains": "|".join(entry.get("local_pack_overlap_domains") or []),
            "top_domains": "|".join(top_domains),
            "paa_questions": "|".join(paa_q[:20]),
            "related_searches": "|".join(rel[:20]),
            "recommended_content": "",
            "recommended_angle": "",
            "semantic_themes": "",
            "status": entry.get("status", "ok") or "ok",
        }

        # If SERP call failed, skip IA but still output the row
        if row["status"] != "ok":
            keywords_rows.append(row)
            serp_overview_rows.extend(build_serp_overview_rows(entry))
            continue

        try:
            prompt = build_prompt(entry)
            ia = call_openai_json(prompt)

            row["intent"] = ia.get("intent", "")
            row["recommended_content"] = ia.get("recommended_content", "")
            row["recommended_angle"] = ia.get("recommended_angle", "")

            themes = ia.get("semantic_themes", [])
            if isinstance(themes, list):
                row["semantic_themes"] = "|".join([str(x) for x in themes][:20])
            else:
                row["semantic_themes"] = str(themes)

        except Exception as e:
            logger.error(f"IA error for '{kw}': {e}")
            row["status"] = "ia_error"

        # deterministic scoring (even if ia_error -> still produce)
        local_intent = compute_local_intent_score(entry)
        difficulty = compute_difficulty_score(entry)
        opportunity = compute_opportunity_score(entry, difficulty, local_intent)

        row["local_intent_score"] = local_intent
        row["experimental_difficulty_proxy"] = difficulty
        row["experimental_opportunity_proxy"] = opportunity

        keywords_rows.append(row)
        serp_overview_rows.extend(build_serp_overview_rows(entry))

    # urls_analysis placeholder (filled by analyze_urls.py)
    urls_rows: List[Dict[str, Any]] = []

    write_xlsx_sheets(
        OUTPUT_XLSX,
        sheets={
            SHEET_KEYWORDS: keywords_rows,
            SHEET_SERP_OVERVIEW: serp_overview_rows,
            SHEET_URLS: urls_rows,
        },
    )

    logger.info(f"XLSX écrit: {OUTPUT_XLSX}")
    logger.info("=== END analyze_openai.py ===")


if __name__ == "__main__":
    main()
