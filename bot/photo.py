"""사진 찾기 — 상업적 이용 가능한 CC/퍼블릭도메인 사진만 사용, 출처 자동 표기.

순서 (사진이 반드시 나오도록 단계별로 넓혀 감):
 1) 위키미디어 커먼즈 — 인물 이름 (이름이 파일명/설명에 있어야 채택)
 2) Openverse(플리커 등 CC 사진 6억 장) — 인물 이름 (제목/태그에 이름 있어야 채택)
 3) 커먼즈·Openverse — 기사 분위기 검색어 (예: 콘서트 무대, 촬영장)
 4) 분야별 기본 검색어 (예: concert stage lights)
"""
import html
import re
from pathlib import Path

import requests

UA = {"User-Agent": "news-insta-bot/1.0 (personal news card bot; contact via GitHub)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
OK_LICENSE = re.compile(r"(cc[- ]?by|cc0|public domain|^pd|pdm|kogl|attribution|^by)", re.I)
BAD_LICENSE = re.compile(r"(nc|nd|fair|non[- ]?commercial)", re.I)

DEFAULT_SCENES = {
    "음악": ["concert stage lights", "concert crowd", "microphone stage"],
    "연예": ["concert stage lights", "red carpet event", "spotlight stage"],
    "드라마": ["film set camera", "television studio", "movie clapperboard"],
    "영화": ["cinema theater seats", "film camera", "movie premiere"],
    "예능": ["television studio", "tv studio lights", "broadcast camera"],
}


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _tokens(q: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", q)]


def commons_search(query: str, limit: int = 15) -> list[dict]:
    r = requests.get(COMMONS, headers=UA, timeout=20, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
    })
    r.raise_for_status()
    out = []
    for p in sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda x: x.get("index", 99)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        out.append({
            "text": p.get("title", "") + " " + _strip(meta.get("ImageDescription", {}).get("value", ""))[:300],
            "ok_type": info.get("mime") in ("image/jpeg", "image/png"),
            "width": info.get("width", 0),
            "url": info.get("thumburl") or info.get("url"),
            "page": info.get("descriptionurl", ""),
            "license": _strip(meta.get("LicenseShortName", {}).get("value", "")),
            "artist": _strip(meta.get("Artist", {}).get("value", ""))[:80] or "Unknown",
            "source": "Wikimedia Commons",
        })
    return out


def openverse_search(query: str) -> list[dict]:
    r = requests.get(OPENVERSE, headers=UA, timeout=25, params={
        "q": query, "license_type": "commercial", "page_size": 20, "mature": "false",
    })
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        lic = f"CC {it.get('license', '').upper()} {it.get('license_version') or ''}".strip()
        if it.get("license") in ("cc0", "pdm"):
            lic = "CC0" if it["license"] == "cc0" else "Public Domain"
        tags = " ".join(t.get("name", "") for t in it.get("tags") or [])
        out.append({
            "text": f"{it.get('title', '')} {tags}",
            "ok_type": (it.get("filetype") or "jpg").lower() in ("jpg", "jpeg", "png"),
            "width": it.get("width") or 1000,
            "url": it.get("url"),
            "page": it.get("foreign_landing_url") or "",
            "license": lic,
            "artist": (it.get("creator") or "Unknown")[:80],
            "source": (it.get("source") or "Openverse").capitalize(),
        })
    return out


def _try(results, need_tokens, save_to):
    for c in results:
        if not c["ok_type"] or c["width"] < 700 or not c["url"]:
            continue
        if not OK_LICENSE.search(c["license"]) or BAD_LICENSE.search(c["license"]):
            continue
        if need_tokens and not all(t in c["text"].lower() for t in need_tokens):
            continue
        try:
            img = requests.get(c["url"], headers=UA, timeout=30)
            img.raise_for_status()
            if len(img.content) < 20_000:
                continue
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_bytes(img.content)
            return c
        except Exception as e:
            print(f"[사진 다운로드 실패] {c['url']}: {e}")
    return None


def find_photo(person_queries, scene_queries, tag, save_to: Path) -> dict | None:
    steps = []
    for q in person_queries or []:
        steps += [("인물", commons_search, q, True), ("인물", openverse_search, q, True)]
    for q in (scene_queries or []) + DEFAULT_SCENES.get(tag, DEFAULT_SCENES["연예"]):
        steps += [("분위기", commons_search, q, False), ("분위기", openverse_search, q, False)]
    for kind, fn, q, strict in steps:
        if not q or not q.strip():
            continue
        try:
            hit = _try(fn(q), _tokens(q) if strict else [], save_to)
        except Exception as e:
            print(f"[사진 검색 실패] {fn.__name__} '{q}': {e}")
            continue
        if hit:
            print(f"[사진/{kind}] '{q}' → {hit['page']} ({hit['license']})")
            return {"path": str(save_to), "credit": hit["artist"], "license": hit["license"],
                    "page": hit["page"], "source": hit["source"], "kind": kind, "query": q}
    print(f"[사진 없음] {person_queries} / {scene_queries}")
    return None
