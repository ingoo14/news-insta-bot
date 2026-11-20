"""지금 뜨는 키워드 수집: 구글 트렌드 실시간 급상승(한국) + 유튜브 인기 급상승(한국).

- 구글 트렌드: 공식 RSS (무료, 키 불필요). 검색량 추정치 + 관련 뉴스 기사 포함
- 유튜브: YouTube Data API 'mostPopular' 차트 (무료 키 필요, 1회 1유닛 / 하루 10,000유닛 무료)
  YOUTUBE_API_KEY 가 없으면 유튜브는 건너뜀
"""
import os
import re
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
GT_FEEDS = ["https://trends.google.com/trending/rss?geo=KR", "https://trends.google.co.kr/trending/rss?geo=KR"]
YT_CATEGORIES = {"10": "음악", "24": "엔터테인먼트", "1": "영화/애니", "": "전체"}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _traffic_num(s: str) -> int:
    """'50K+', '2만+', '1,000+' → 숫자"""
    s = (s or "").replace(",", "").replace("+", "").strip()
    m = re.match(r"([\d.]+)\s*([KkMm만천]?)", s)
    if not m:
        return 0
    n = float(m.group(1))
    mult = {"k": 1e3, "m": 1e6, "만": 1e4, "천": 1e3}.get(m.group(2).lower(), 1)
    return int(n * mult)


def google_trends() -> list[dict]:
    for url in GT_FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"[구글 트렌드 실패] {url}: {e}")
            continue
        out = []
        for item in root.iter():
            if _local(item.tag) != "item":
                continue
            t = {"term": "", "traffic": "", "news": []}
            for ch in item:
                n = _local(ch.tag)
                if n == "title":
                    t["term"] = (ch.text or "").strip()
                elif n == "approx_traffic":
                    t["traffic"] = (ch.text or "").strip()
                elif n == "news_item":
                    ni = {_local(x.tag): (x.text or "").strip() for x in ch}
                    if ni.get("news_item_url"):
                        t["news"].append({"title": ni.get("news_item_title", ""), "url": ni["news_item_url"],
                                          "source": ni.get("news_item_source", "")})
            if t["term"]:
                t["score"] = _traffic_num(t["traffic"])
                t["source"] = "google"
                out.append(t)
        print(f"[구글 트렌드] {len(out)}개: " + ", ".join(f"{x['term']}({x['traffic']})" for x in out[:15]))
        return out
    return []


def youtube_trending() -> list[dict]:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("[유튜브 트렌드] YOUTUBE_API_KEY 없음 → 건너뜀")
        return []
    out, seen = [], set()
    for cat, label in YT_CATEGORIES.items():
        params = {"part": "snippet,statistics", "chart": "mostPopular", "regionCode": "KR",
                  "maxResults": 50, "key": key, "hl": "ko"}
        if cat:
            params["videoCategoryId"] = cat
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params, timeout=20)
            if r.status_code >= 400:
                print(f"[유튜브 {label}] {r.status_code} {r.text[:150]}")
                continue
            items = r.json().get("items", [])
        except Exception as e:
            print(f"[유튜브 {label} 실패] {e}")
            continue
        for rank, v in enumerate(items, 1):
            if v["id"] in seen:
                continue
            seen.add(v["id"])
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            out.append({"term": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                        "views": int(st.get("viewCount", 0) or 0), "rank": rank, "category": label,
                        "score": max(0, 60 - rank) * 1000, "source": "youtube", "news": []})
    print(f"[유튜브 트렌드] {len(out)}개: " + ", ".join(f"{x['channel']}" for x in out[:15]))
    return out


def collect() -> list[dict]:
    return google_trends() + youtube_trending()


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def score_candidates(cands: list[dict], trends: list[dict]) -> None:
    """기사 제목/요약에 트렌드 키워드(구글 검색어, 유튜브 채널명)가 들어가면 점수 부여."""
    keys = []
    for t in trends:
        for k in [t["term"]] if t["source"] == "google" else [t.get("channel", "")]:
            nk = _norm(k)
            if len(nk) >= 2:
                keys.append((nk, t))
    for c in cands:
        hay = _norm(c["title"] + " " + c.get("snippet", ""))
        hits = [(t["score"], t) for nk, t in keys if nk in hay]
        c["trend_score"] = sum(s for s, _ in hits)
        c["trend_hits"] = [f"{'구글' if t['source'] == 'google' else '유튜브'}:{t.get('term') if t['source'] == 'google' else t.get('channel')}"
                           f"{'(' + t['traffic'] + ')' if t.get('traffic') else ''}" for _, t in hits][:3]
        c["trend_label"] = next((f"검색 {t['traffic']}" for _, t in hits if t.get("traffic")), "")
        if not c["trend_label"] and hits:
            c["trend_label"] = "유튜브 인기 급상승"


def trend_news_candidates(trends: list[dict]) -> list[dict]:
    """구글 트렌드 키워드에 붙어 있는 관련 기사도 후보로 추가."""
    from datetime import datetime, timezone
    from .news import press_name
    out = []
    for t in trends:
        for n in t.get("news", [])[:2]:
            out.append({"title": n["title"], "snippet": f"(구글 트렌드 급상승: {t['term']} {t['traffic']})",
                        "url": n["url"], "press": n.get("source") or press_name(n["url"]),
                        "published": datetime.now(timezone.utc).isoformat()})
    return out


def summary_for_prompt(trends: list[dict], n: int = 40) -> str:
    g = [t for t in trends if t["source"] == "google"][:25]
    y = [t for t in trends if t["source"] == "youtube"][:25]
    lines = [f"- [구글 검색 급상승] {t['term']} ({t['traffic']})" for t in g]
    lines += [f"- [유튜브 인기 {t['category']} {t['rank']}위] {t['channel']} — {t['term'][:50]}" for t in y]
    return "\n".join(lines[:n]) or "(트렌드 데이터 없음)"


STOP = set("""단독 종합 공식 포토 영상 화보 인터뷰 오늘 공개 이번 무대 사진 기자 뉴스 연예 가수 배우 그룹 신곡 컴백
첫 새 더 위해 대한 통해 관련 이후 지난 최근 현장 출연 방송 발표 진행 시작 화제 눈길 근황 소식 결국 직접 모습""".split())


def hot_keywords(cands: list[dict], hours: float = 2.0, min_count: int = 4) -> list[tuple[str, int]]:
    """최근 N시간 기사 제목에 몰려서 나오는 단어 = 지금 실시간으로 벌어지는 이벤트 (예: MAMA, 시상식, 컴백 쇼케이스)."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    cnt = Counter()
    for c in cands:
        if datetime.fromisoformat(c["published"]) < cut:
            continue
        words = set(re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", c["title"]))
        for w in words:
            w2 = w.upper() if w.isascii() else w
            if w2 not in STOP and len(w2) >= 2:
                cnt[w2] += 1
    return [(w, n) for w, n in cnt.most_common(12) if n >= min_count]


MUSIC = re.compile(r"(음원|음반|앨범|신곡|컴백|차트|멜론|콘서트|투어|무대|가요|뮤직|MAMA|마마|시상식|가수|아이돌|그룹|싱글|"
                   r"뮤비|MV|팬미팅|빌보드|OST|래퍼|밴드|보컬|데뷔|쇼케이스|음악)", re.I)
DRAMA = re.compile(r"(드라마|시청률|방영|넷플릭스|티빙|디즈니|쿠팡|웨이브|주연|캐스팅|출연|회차)")
MOVIE = re.compile(r"(영화|개봉|관객|박스오피스|감독|극장|시사회|천만)")


def score_freshness(cands: list[dict], hot: list[tuple[str, int]]) -> None:
    """실시간성 + 분야(음악 > 드라마 > 영화 > 기타) + 실시간 이벤트 키워드 점수."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    hotset = {w for w, _ in hot}
    for c in cands:
        mins = max(0, (now - datetime.fromisoformat(c["published"])).total_seconds() / 60)
        c["age_min"] = int(mins)
        fresh = 30000 if mins <= 60 else 18000 if mins <= 120 else 8000 if mins <= 240 else 0
        t = c["title"] + " " + c.get("snippet", "")
        cat = 15000 if MUSIC.search(t) else 9000 if DRAMA.search(t) else 6000 if MOVIE.search(t) else 0
        words = {w.upper() if w.isascii() else w for w in re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", c["title"])}
        live = 25000 if words & hotset else 0
        c["live_hits"] = sorted(words & hotset)
        c["score"] = c.get("trend_score", 0) + fresh + cat + live
