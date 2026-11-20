"""수동 업로드: 텔레그램 봇에 기사 링크를 보내면 → 카드 2장 만들어서 업로드 대기열에 넣음.
(또는 GitHub Actions에서 '링크로 바로 올리기' 실행 시 URL 입력)

5분마다 텔레그램 새 메시지 확인 → 링크가 있으면 처리. 업로드는 bot.publish 가 이어서 함.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from . import collect, config, news, photo, render, summarize, telegram

KST = timezone(timedelta(hours=9))
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def article_from_url(url: str) -> dict:
    r = requests.get(url, headers=news.UA, timeout=15)
    r.encoding = r.apparent_encoding or r.encoding
    soup = BeautifulSoup(r.text, "html.parser")

    def meta(*names):
        for n in names:
            m = soup.find("meta", property=n) or soup.find("meta", attrs={"name": n})
            if m and m.get("content"):
                return m["content"].strip()
        return ""

    title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
    press = meta("og:article:author", "og:site_name", "twitter:creator") or news.press_name(r.url)
    item = {"title": title, "snippet": meta("og:description", "description"), "url": r.url,
            "press": press, "published": datetime.now(timezone.utc).isoformat()}
    item["body"] = news.fetch_body(item)
    return item


# 채널 별명 (메시지에 이 단어가 있으면 그 채널로)
ALIASES = {
    "ibduck_music": ["입덕", "ibduck"],
    "greenlight_music": ["그린", "greenlight"],
    "gaem_seong_1spoon": ["갬성", "겜성", "gaem"],
}


def pick_channel(text: str) -> dict:
    chans = config.channels()
    t = (text or "").lower()
    for c in chans:
        h = c.get("handle", "").lower()
        keys = [h] + ALIASES.get(h, []) + [w for w in h.replace(".", "_").split("_") if len(w) >= 4]
        if any(k and k in t for k in keys):
            return c
    return chans[0] if chans else {"key": "ch1", "handle": "메인"}


CREDIT_RE = re.compile(r"(?:사진|photo)\s*[:：]\s*([^\n]+)", re.I)


def make_post(url: str, ch: dict | None = None, user_photo: dict | None = None) -> tuple[bool, str]:
    """user_photo = {"file_id": ..., "credit": ...} 이면 자동 검색 대신 보내준 사진 사용."""
    ch = ch or pick_channel("")
    try:
        a = article_from_url(url)
    except Exception as e:
        return False, f"기사를 못 불러왔어: {e}"
    if not a["title"]:
        return False, "기사 제목을 못 찾았어 (링크 확인)"
    s = summarize.write_story(a)
    if not (s.get("headline_top") and s.get("body")):
        return False, "요약을 못 만들었어 (본문이 비어 있는 기사일 수 있음)"
    now = datetime.now(KST)
    post_id = f"{now:%Y%m%d-%H%M%S}-m-{ch['key']}"
    out_dir = config.POSTS_DIR / post_id
    if user_photo:
        try:
            telegram.download_file(user_photo["file_id"], out_dir / "_src.jpg")
            ph = {"path": str(out_dir / "_src.jpg"), "credit": user_photo.get("credit", ""), "license": "",
                  "page": "", "source": "", "kind": "직접 전송"}
        except Exception as e:
            return False, f"보내준 사진을 못 받았어: {e}"
    else:
        names = [s.get("person", "")] + list(s.get("photo_queries", []))
        ph = photo.find_photo([n for i, n in enumerate(names) if n and n not in names[:i]],
                              s.get("scene_queries", []), s.get("tag", "연예"), out_dir / "_src.jpg")
    if not ph:
        return False, f"‘{s.get('person') or a['title']}’ 인물의 무료 사진을 못 찾아서 못 올렸어"
    s["issue_no"] = collect._next_issue()
    paths = render.render_story(s, ph, out_dir)
    (out_dir / "_src.jpg").unlink(missing_ok=True)
    record = {"id": post_id, "created": now.isoformat(), "status": "pending", "attempts": 0, "manual": True,
              "images": [str(p.relative_to(config.ROOT)) for p in paths],
              "caption": collect.build_caption(s, ph), "story": s, "photo": ph,
              "channel": ch["key"], "channel_handle": ch.get("handle", "")}
    config.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    (config.PENDING_DIR / f"{post_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    # 같은 소식이 자동 수집으로 또 올라가지 않게 기록
    seen = collect._load(config.SEEN_FILE, []) + [a["url"], url]
    config.SEEN_FILE.write_text(json.dumps(seen[-3000:], ensure_ascii=False))
    topics = collect._load(config.TOPICS_FILE, []) + [f"{s.get('title', '')} ({now:%m/%d})"]
    config.TOPICS_FILE.write_text(json.dumps(topics[-60:], ensure_ascii=False, indent=1))
    return True, s.get("title", a["title"])


def telegram_links() -> list[tuple[str, str, dict | None]]:
    """내 채팅방에서 봇에게 보낸 새 메시지 중 (링크, 메시지 전체, 첨부 사진) 꺼냄.
    사진을 보낼 땐 사진 설명(캡션)에 링크를 같이 넣기. '사진: 출처' 를 쓰면 출처로 표기."""
    try:
        offset = int(config.OFFSET_FILE.read_text().strip() or 0)
    except Exception:
        offset = 0
    updates = telegram.get_updates(offset + 1 if offset else 0)
    urls = []
    for u in updates:
        offset = max(offset, u["update_id"])
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
            continue  # 다른 사람이 보낸 건 무시
        text = msg.get("text", "") or msg.get("caption", "") or ""
        up = None
        if msg.get("photo"):  # 압축 사진 → 가장 큰 크기
            up = {"file_id": msg["photo"][-1]["file_id"]}
        elif (msg.get("document") or {}).get("mime_type", "").startswith("image/"):  # 파일로 보낸 원본 사진
            up = {"file_id": msg["document"]["file_id"]}
        if up:
            m = CREDIT_RE.search(text)
            up["credit"] = m.group(1).strip() if m else ""
        urls += [(u, text, up) for u in URL_RE.findall(text)]
    config.OFFSET_FILE.write_text(str(offset))
    return urls


def main():
    for k in ["GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        config.env(k, required=True)
    manual_in = os.environ.get("MANUAL_URL", "")
    urls = [(u, manual_in, None) for u in URL_RE.findall(manual_in)]
    # 텔레그램 → Cloudflare → GitHub 로 바로 들어온 메시지
    tg_text, tg_file = os.environ.get("TG_TEXT", ""), os.environ.get("TG_FILE_ID", "")
    if tg_text:
        up = None
        if tg_file:
            m = CREDIT_RE.search(tg_text)
            up = {"file_id": tg_file, "credit": m.group(1).strip() if m else ""}
        urls += [(u, tg_text, up) for u in URL_RE.findall(tg_text)]
    # (예전 방식) webhook을 안 쓸 때만 텔레그램에서 직접 새 메시지 가져오기
    if os.environ.get("TG_WEBHOOK") != "1":
        urls += telegram_links()
    if not urls:
        print("새 링크 없음")
        return
    done = set()
    for url, text, up in urls:
        if url in done:
            continue
        done.add(url)
        ch = pick_channel(text)
        telegram.send_message(f"🛠 접수! [{ch.get('handle')}] 카드 만드는 중…" + (" (보낸 사진 사용)" if up else "") + f"\n{url}")
        ok, msg = make_post(url, ch, up)
        print(("[준비 완료] " if ok else "[실패] ") + msg)
        if not ok:
            telegram.send_message(f"⚠️ {msg}\n{url}")


if __name__ == "__main__":
    main()
