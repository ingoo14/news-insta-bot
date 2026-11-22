"""연예 뉴스 수집: 언론사 연예 RSS (가입·키 불필요) + 기사 본문 추출."""
import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import config

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}

# 언론사 연예 RSS — 일부가 막혀도 나머지로 계속 돌아감. GitHub Variables의 NEWS_FEEDS로 교체 가능(쉼표 구분)
DEFAULT_FEEDS = [
    "https://www.yna.co.kr/rss/entertainment.xml",
    "https://www.segye.com/Articles/RSSList/segye_entertainment.xml",
    "https://www.sportsworldi.com/Articles/RSSList/sw_entertainment.xml",
    "https://mydaily.co.kr/ent_rss.xml",
    "https://mydaily.co.kr/music_rss.xml",
    "https://mydaily.co.kr/tv_rss.xml",
    "https://www.hankyung.com/feed/entertainment",
    "https://www.chosun.com/arc/outboundfeeds/rss/category/entertainments/?outputType=xml",
]
FEEDS = [f.strip() for f in (os.environ.get("NEWS_FEEDS") or ",".join(DEFAULT_FEEDS)).split(",") if f.strip()]

PRESS = {
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1", "osen.co.kr": "OSEN",
    "xportsnews.com": "엑스포츠뉴스", "mydaily.co.kr": "마이데일리", "starnewskorea.com": "스타뉴스",
    "sportschosun.com": "스포츠조선", "sports.donga.com": "스포츠동아", "isplus.com": "일간스포츠",
    "sportsseoul.com": "스포츠서울", "tenasia.co.kr": "텐아시아", "tvreport.co.kr": "TV리포트",
    "joynews24.com": "조이뉴스24", "inews24.com": "아이뉴스24", "mk.co.kr": "매일경제", "hankyung.com": "한국경제",
    "edaily.co.kr": "이데일리", "heraldcorp.com": "헤럴드경제", "sedaily.com": "서울경제",
    "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "kbs.co.kr": "KBS", "imbc.com": "MBC",
    "sbs.co.kr": "SBS", "jtbc.co.kr": "JTBC", "dispatch.co.kr": "디스패치", "topstarnews.net": "톱스타뉴스",
    "sportsworldi.com": "스포츠월드", "segye.com": "세계일보", "spotvnews.co.kr": "스포티비뉴스",
}


def press_name(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    for dom, name in PRESS.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def title_key(title: str) -> str:
    """제목 중복 판별용 키 (공백·특수문자 제거 후 앞 18자)."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)[:18]


def _date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = parsedate_to_datetime(text)
    except Exception:
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=9)))


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def fetch_feed(url: str) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        f = {}
        for ch in node:
            name = _local(ch.tag)
            if name == "link" and ch.get("href"):
                f.setdefault("link", ch.get("href"))
            elif ch.text and name not in f:
                f[name] = ch.text
        link = (f.get("link") or "").strip()
        title = _clean(f.get("title"))
        if not link or not title:
            continue
        d = _date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        out.append({
            "title": title,
            "snippet": _clean(f.get("description") or f.get("summary") or "")[:300],
            "url": link,
            "press": press_name(link),
            "published": (d or datetime.now(timezone.utc)).isoformat(),
        })
    return out


def collect_candidates(seen_urls: set[str]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.NEWS_LOOKBACK_HOURS)
    result, keys, urls = [], set(), set()
    for feed in FEEDS:
        try:
            items = fetch_feed(feed)
            print(f"[수집] {feed} → {len(items)}건")
        except Exception as e:  # 한 곳 실패해도 계속
            print(f"[수집 실패] {feed}: {e}")
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


BODY_SELECTORS = ["div.art_body", "#articleWrap", "#articeBody", "#articleBody", "div.article-txt", "div.story-news",
                  "#article_txt", "div.article_body", "div#article-view-content-div", "section.article-body",
                  "div.news_body", "article"]


def fetch_body(item: dict, limit: int = 2000) -> str:
    """기사 본문을 최대한 뽑아오고, 실패하면 RSS 요약으로 대체."""
    try:
        r = requests.get(item["url"], headers=UA, timeout=12)
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
        print(f"[본문 실패] {item['url']}: {e}")
    return item.get("snippet", "")
