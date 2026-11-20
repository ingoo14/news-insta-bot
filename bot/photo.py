"""사진 찾기 — 상업적 이용 가능한 CC/퍼블릭도메인 사진만 사용, 출처 자동 표기.

기사 인물과 맞는 사진을 우선 (순서대로):
 1) 위키데이터 — 한글 이름으로 그 인물(그룹) 항목을 정확히 찾고, 등록된 '대표 사진' 사용
 2) 위키미디어 커먼즈 — 그 인물의 사진 폴더(카테고리)에서 최신 사진
 3) 커먼즈·Openverse 이름 검색 (파일명/태그에 이름이 있어야 채택)
 4) (PHOTO_STRICT=false 일 때만) 분위기 사진 — 기본값은 인물 사진 없으면 그 기사 건너뜀
"""
import os
import html
import re
from pathlib import Path

import requests

UA = {"User-Agent": "news-insta-bot/1.0 (personal news card bot; contact via GitHub)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
PERSONISH = re.compile(r"(가수|배우|그룹|아이돌|래퍼|밴드|코미디언|방송인|모델|감독|프로듀서|작곡가|"
                       r"singer|actor|actress|group|band|rapper|idol|entertainer|comedian|presenter|model|director|"
                       r"musician|producer|duo|boy band|girl group)", re.I)
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


def _try(results, need_tokens, save_to, check=None):
    for c in results:
        if check and not check(c):
            continue
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


def _commons_files(titles: list[str]) -> list[dict]:
    if not titles:
        return []
    r = requests.get(COMMONS, headers=UA, timeout=20, params={
        "action": "query", "format": "json", "titles": "|".join(titles[:40]),
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata|timestamp", "iiurlwidth": 1600,
    })
    r.raise_for_status()
    out = []
    for p in r.json().get("query", {}).get("pages", {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        date = _strip(meta.get("DateTimeOriginal", {}).get("value", "")) or info.get("timestamp", "")
        out.append({
            "text": p.get("title", ""), "ok_type": info.get("mime") in ("image/jpeg", "image/png"),
            "width": info.get("width", 0), "height": info.get("height", 0),
            "url": info.get("thumburl") or info.get("url"), "page": info.get("descriptionurl", ""),
            "license": _strip(meta.get("LicenseShortName", {}).get("value", "")),
            "artist": _strip(meta.get("Artist", {}).get("value", ""))[:80] or "Unknown",
            "source": "Wikimedia Commons", "date": date,
        })
    return out


def wikidata_person(name: str) -> dict | None:
    """한글/영문 이름 → 위키데이터 인물·그룹 항목 (대표사진 P18, 커먼즈 카테고리 P373)."""
    for lang in ("ko", "en"):
        r = requests.get(WIKIDATA, headers=UA, timeout=20, params={
            "action": "wbsearchentities", "search": name, "language": lang, "uselang": "ko",
            "type": "item", "limit": 7, "format": "json"})
        r.raise_for_status()
        hits = r.json().get("search", [])
        if not hits:
            continue
        ids = [h["id"] for h in hits]
        desc = {h["id"]: (h.get("description") or "") for h in hits}
        e = requests.get(WIKIDATA, headers=UA, timeout=20, params={
            "action": "wbgetentities", "ids": "|".join(ids), "props": "claims|descriptions",
            "languages": "ko|en", "format": "json"}).json().get("entities", {})
        for qid in ids:  # 검색 순위 순서 유지
            ent = e.get(qid, {})
            claims = ent.get("claims", {})
            p31 = {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in claims.get("P31", [])}
            d = desc[qid] + " " + " ".join(v.get("value", "") for v in ent.get("descriptions", {}).values())
            if not ("Q5" in p31 or PERSONISH.search(d)):
                continue
            def val(p):
                c = claims.get(p)
                return c[0]["mainsnak"].get("datavalue", {}).get("value") if c else None
            info = {"qid": qid, "image": val("P18"), "category": val("P373"), "desc": d.strip()}
            info.update(group_members(claims))
            return info
    return None


def _time(v):
    """위키데이터 시간값 '+2022-08-01T00:00:00Z' → '2022-08-01'"""
    try:
        return v["time"].lstrip("+")[:10].replace("-00", "-01")
    except Exception:
        return ""


def group_members(claims: dict) -> dict:
    """그룹이면 멤버 목록 (P527 '구성원', 종료일 P582 있으면 탈퇴 멤버)."""
    former, current, last_change = [], [], ""
    for c in claims.get("P527", []):
        mid = c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
        if not mid:
            continue
        q = c.get("qualifiers", {})
        end = _time(q["P582"][0]["datavalue"]["value"]) if q.get("P582") and q["P582"][0].get("datavalue") else ""
        start = _time(q["P580"][0]["datavalue"]["value"]) if q.get("P580") and q["P580"][0].get("datavalue") else ""
        (former if end else current).append(mid)
        last_change = max(last_change, end, start)  # 마지막 멤버 변동(탈퇴·합류) 날짜
    if not (former or current):
        return {}
    names = _labels(former) if former else {}
    return {"former_ids": former, "former_names": names, "member_change": last_change}


def _labels(qids: list[str]) -> dict:
    """QID → [한글 이름, 영문 이름, 별칭들]"""
    out = {}
    try:
        e = requests.get(WIKIDATA, headers=UA, timeout=20, params={
            "action": "wbgetentities", "ids": "|".join(qids[:40]), "props": "labels|aliases",
            "languages": "ko|en", "format": "json"}).json().get("entities", {})
        for q, ent in e.items():
            names = [v["value"] for v in ent.get("labels", {}).values()]
            names += [a["value"] for lst in ent.get("aliases", {}).values() for a in lst][:6]
            out[q] = [n for n in names if len(n) >= 2]
    except Exception as ex:
        print(f"[멤버 이름 조회 실패] {ex}")
    return out


def former_member_ok(c: dict, wd: dict) -> bool:
    """탈퇴 멤버가 찍혔을 수 있는 사진 거르기:
    1) 파일명/설명에 탈퇴 멤버 이름이 있으면 제외
    2) 멤버 변동이 있었던 그룹이면 '마지막 변동일 이후' 찍힌 사진만 사용 (날짜 모르면 제외)"""
    text = c.get("text", "").lower()

    def hit(n):
        n = n.lower()
        if n.isascii():  # 영문 이름은 단어 단위로만 (짧은 성씨 'Kim' 같은 오탐 방지)
            return len(n) >= 4 and re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", text)
        return n in text
    for names in (wd.get("former_names") or {}).values():
        if any(hit(n) for n in names):
            print(f"[탈퇴 멤버 이름 포함 → 제외] {c.get('page')}")
            return False
    change = wd.get("member_change")
    if wd.get("former_ids") and change:
        date = (c.get("date") or "")[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}", date) or date < change:
            print(f"[멤버 변동({change}) 이전/날짜 불명 사진 → 제외] {c.get('page')} ({date or '날짜 없음'})")
            return False
    return True


def commons_category_files(category: str, limit: int = 30) -> list[str]:
    r = requests.get(COMMONS, headers=UA, timeout=20, params={
        "action": "query", "format": "json", "list": "categorymembers", "cmtitle": f"Category:{category}",
        "cmtype": "file", "cmlimit": limit, "cmsort": "timestamp", "cmdir": "desc"})
    r.raise_for_status()
    return [m["title"] for m in r.json().get("query", {}).get("categorymembers", [])]


def _pack(hit, save_to, kind, q):
    print(f"[사진/{kind}] '{q}' → {hit['page']} ({hit['license']})")
    return {"path": str(save_to), "credit": hit["artist"], "license": hit["license"],
            "page": hit["page"], "source": hit["source"], "kind": kind, "query": q}


def find_person_photo(names: list[str], save_to: Path) -> dict | None:
    known = {}
    for name in [n for n in names if n and n.strip()]:
        try:
            wd = wikidata_person(name)
        except Exception as e:
            print(f"[위키데이터 실패] {name}: {e}")
            wd = None
        if wd:
            known[name] = wd
            if wd.get("former_ids"):
                fn_ = ", ".join(v[0] for v in wd.get("former_names", {}).values() if v)
                print(f"[그룹 멤버 변동] 탈퇴: {fn_} / 마지막 변동일: {wd.get('member_change')}")
            print(f"[위키데이터] {name} → {wd['qid']} {wd['desc'][:40]} / 사진:{wd['image']} / 폴더:{wd['category']}")
            try:
                # 사진 폴더의 최신 사진 우선(요즘 모습), 없으면 대표 사진
                files = commons_category_files(wd["category"]) if wd.get("category") else []
                cands = _commons_files(files)
                cands.sort(key=lambda c: c.get("date", ""), reverse=True)
                if wd.get("image"):
                    cands += _commons_files([f"File:{wd['image']}"])
                hit = _try(cands, [], save_to, check=lambda c, wd=wd: former_member_ok(c, wd))
                if hit:
                    return _pack(hit, save_to, "인물", name)
            except Exception as e:
                print(f"[커먼즈 실패] {name}: {e}")
    # 위키데이터에 없으면 이름 검색 (파일명/태그에 이름 포함된 것만)
    for name in names or []:
        wd_known = known.get(name)
        for fn in (commons_search, openverse_search):
            if wd_known and wd_known.get("former_ids") and fn is openverse_search:
                continue  # 탈퇴 멤버 있는 그룹은 날짜 확인이 어려운 Openverse 사진은 안 씀
            try:
                hit = _try(fn(name), _tokens(name), save_to,
                           check=(lambda c, wd=wd_known: former_member_ok(c, wd)) if wd_known else None)
            except Exception as e:
                print(f"[사진 검색 실패] {fn.__name__} '{name}': {e}")
                continue
            if hit:
                return _pack(hit, save_to, "인물", name)
    return None


def find_photo(person_queries, scene_queries, tag, save_to: Path) -> dict | None:
    ph = find_person_photo(person_queries or [], save_to)
    if ph:
        return ph
    if (os.environ.get("PHOTO_STRICT") or "true").lower() != "false":
        print(f"[사진 없음 → 기사 건너뜀] {person_queries}")
        return None
    for q in (scene_queries or []) + DEFAULT_SCENES.get(tag, DEFAULT_SCENES["연예"]):
        for fn in (commons_search, openverse_search):
            try:
                hit = _try(fn(q), [], save_to)
            except Exception as e:
                print(f"[사진 검색 실패] {fn.__name__} '{q}': {e}")
                continue
            if hit:
                return _pack(hit, save_to, "분위기", q)
    print(f"[사진 없음] {person_queries} / {scene_queries}")
    return None
