"""연예 뉴스 수집: 네이버 검색 API + 기사 본문 추출."""
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import config

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}

PRESS = {
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1", "osen.co.kr": "OSEN",
    "xportsnews.com": "엑스포츠뉴스", "mydaily.co.kr": "마이데일리", "starnewskorea.com": "스타뉴스",
    "sportschosun.com": "스포츠조선", "sports.donga.com": "스포츠동아", "isplus.com": "일간스포츠",
    "sportsseoul.com": "스포츠서울", "tenasia.co.kr": "텐아시아", "tvreport.co.kr": "TV리포트",
    "joynews24.com": "조이뉴스24", "mk.co.kr": "매일경제", "hankyung.com": "한국경제",
    "edaily.co.kr": "이데일리", "heraldcorp.com": "헤럴드경제", "sedaily.com": "서울경제",
    "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "kbs.co.kr": "KBS", "imbc.com": "MBC",
    "sbs.co.kr": "SBS", "jtbc.co.kr": "JTBC", "dispatch.co.kr": "디스패치", "topstarnews.net": "톱스타뉴스",
    "sportsworldi.com": "스포츠월드", "spotvnews.co.kr": "스포티비뉴스", "mhns.co.kr": "문화뉴스",
    "ajunews.com": "아주경제", "fnnews.com": "파이낸셜뉴스", "asiae.co.kr": "아시아경제",
}


def press_name(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    for dom, name in PRESS.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def title_key(title: str) -> str:
    """제목 중복 판별용 키 (공백·특수문자 제거 후 앞 18자)."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)[:18]


def fetch_naver(query: str, display: int = 50) -> list[dict]:
    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        headers={"X-Naver-Client-Id": config.NAVER_CLIENT_ID, "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET},
        params={"query": query, "display": display, "sort": "date"},
        timeout=15,
    )
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        url = it.get("originallink") or it.get("link")
        out.append({
            "title": _clean(it.get("title")),
            "snippet": _clean(it.get("description")),
            "url": url,
            "naver_url": it.get("link"),
            "press": press_name(url),
            "published": parsedate_to_datetime(it["pubDate"]).isoformat(),
        })
    return out


def collect_candidates(seen_urls: set[str]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.NEWS_LOOKBACK_HOURS)
    result, keys, urls = [], set(), set()
    for q in config.NEWS_QUERIES:
        try:
            items = fetch_naver(q)
        except Exception as e:  # 한 키워드 실패해도 계속
            print(f"[수집 실패] {q}: {e}")
            continue
        for it in items:
            if datetime.fromisoformat(it["published"]) < cutoff:
                continue
            k = title_key(it["title"])
            if it["url"] in seen_urls or it["url"] in urls or k in keys:
                continue
            keys.add(k)
            urls.add(it["url"])
            result.append(it)
    result.sort(key=lambda x: x["published"], reverse=True)
    return result


BODY_SELECTORS = ["#dic_area", "#articeBody", "#articleBodyContents", "#newsEndContents",
                  "div.article_body", "div#article-view-content-div", "article"]


def fetch_body(item: dict, limit: int = 2000) -> str:
    """기사 본문을 최대한 뽑아오고, 실패하면 스니펫으로 대체."""
    for url in filter(None, [item.get("naver_url"), item.get("url")]):
        try:
            r = requests.get(url, headers=UA, timeout=12)
            r.encoding = r.apparent_encoding or r.encoding
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "figure", "figcaption", "iframe"]):
                tag.decompose()
            text = ""
            for sel in BODY_SELECTORS:
                node = soup.select_one(sel)
                if node and len(node.get_text(strip=True)) > 200:
                    text = node.get_text("\n", strip=True)
                    break
            if not text:
                ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
                text = "\n".join(p for p in ps if len(p) > 40)
            if not text:
                og = soup.find("meta", property="og:description")
                text = og["content"] if og and og.get("content") else ""
            if len(text) > 150:
                return re.sub(r"\n{2,}", "\n", text)[:limit]
        except Exception as e:
            print(f"[본문 실패] {url}: {e}")
    return item.get("snippet", "")
