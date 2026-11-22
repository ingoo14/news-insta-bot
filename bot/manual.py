"""수동 업로드: 텔레그램 봇에 기사 링크를 보내면 → 카드 2장 만들어서 업로드 대기열에 넣음.
(또는 GitHub Actions에서 '링크로 바로 올리기' 실행 시 URL 입력)

5분마다 텔레그램 새 메시지 확인 → 링크가 있으면 처리. 업로드는 bot.publish 가 이어서 함.
"""
import json
from PIL import Image
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from . import collect, config, news, photo, render, summarize, telegram

KST = timezone(timedelta(hours=9))
PUBLISH_AT = ""  # 예약 업로드 시각 (워커가 '예약 20시' 같은 문구를 읽어서 AT=... 으로 넘겨줌)


def _at_label(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(KST).strftime("%m/%d %H:%M")
    except Exception:
        return iso
URL_RE = re.compile(r"https?://[^\s<>\"']+")


PORTAL = re.compile(r"(네이버|naver|다음|daum|카카오|kakao|zum|nate|네이트)", re.I)


def _press(soup, meta_press: str, url: str) -> str:
    """언론사 이름. 네이버·다음 같은 포털 주소면 기사 안의 [OO뉴스=기자] 표기나 언론사 로고에서 찾음."""
    mp = re.sub(r"\s*[|·-]\s*(네이버|NAVER|다음|Daum).*$", "", meta_press or "").strip()
    if mp and not PORTAL.search(mp) and "." not in mp:
        return mp
    for img in soup.select("img[class*=press], img[class*=logo], a[class*=press] img, [class*=press_logo] img"):
        alt = (img.get("alt") or img.get("title") or "").strip()
        if alt and not PORTAL.search(alt) and len(alt) <= 20:
            return alt
    txt = soup.get_text(" ", strip=True)[:6000]
    m = re.search(r"[\[(]\s*([가-힣A-Za-z0-9 ]{2,15}?)\s*=\s*[가-힣 ]{2,10}기자\s*[\])]", txt) or \
        re.search(r"[\[(]\s*(?:[가-힣]{2,4}=)?([가-힣A-Za-z0-9 ]{2,15}?)\s*[\])]\s*[가-힣]{2,4} 기자", txt)
    if m:
        return m.group(1).strip()
    return mp if mp and not PORTAL.search(mp) else news.press_name(url)


CAP_RE = re.compile(r"(?:출처|사진|제공|Photo|ⓒ|©)\s*[|:=│ㅣ]?\s*([^\n▲△|]{2,40})", re.I)


def _photo_caption(soup) -> str:
    """기사 사진 설명에 적힌 사진 출처 (예: '▲ 지드래곤. 출처| 2026 MAMA 캡처' → '2026 MAMA 캡처')."""
    nodes = soup.select("figcaption, em.img_desc, .img_desc, .end_photo_org em, [class*=caption], "
                        "[class*=photo_desc], [class*=img_txt], [class*=imgtxt]")
    for n in nodes:
        t = n.get_text(" ", strip=True)
        if not t or len(t) > 150:
            continue
        m = CAP_RE.search(t)
        if m:
            src = re.sub(r"^(출처|사진)\s*", "", m.group(1)).strip(" .,=")
            src = re.sub(r"\s*(무단|재배포|Copyright).*$", "", src, flags=re.I).strip()
            if len(src) >= 2:
                return src[:30]
    return ""


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
    title = re.sub(r"\s*[:|\-]\s*(네이버|NAVER|Daum|다음)\s*(엔터|연예|뉴스)?\s*$", "", title)
    press = _press(soup, meta("og:article:author", "article:author", "dable:author", "og:site_name", "twitter:creator"), r.url)
    item = {"title": title, "snippet": meta("og:description", "description"), "url": r.url,
            "press": press, "image": meta("og:image", "twitter:image"), "photo_src": _photo_caption(soup), "published": datetime.now(timezone.utc).isoformat()}
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
    t = URL_RE.sub(" ", text or "").lower()  # 링크 주소 안의 글자(예: /music/)는 채널 이름으로 안 봄
    generic = {"music", "news", "official", "daily"}
    for c in chans:
        h = c.get("handle", "").lower()
        keys = [h] + ALIASES.get(h, []) + [w for w in h.replace(".", "_").split("_") if len(w) >= 4 and w not in generic]
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
    body_len = len(a.get("body") or "")
    print(f"[본문] {body_len}자 / {a['press']} / {a['title']}")
    if body_len < 80:
        return False, f"기사 본문을 못 읽었어 ({body_len}자). 이 사이트가 본문을 막아둔 것 같아 — 같은 소식의 다른 언론사 링크로 보내줘"
    s = summarize.write_story(a, manual=True)
    if not (s.get("headline_top") and s.get("body")):
        why = s.get("reason") or "AI가 이 기사로는 글을 못 만들겠다고 함"
        return False, f"요약을 못 만들었어 — {why}"
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
    elif a.get("image"):  # 링크 업로드: 사진을 안 보냈으면 기사 대표 사진 사용
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            img = a["image"] if a["image"].startswith("http") else requests.compat.urljoin(a["url"], a["image"])
            rr = requests.get(img, headers={**news.UA, "Referer": a["url"]}, timeout=20)
            rr.raise_for_status()
            if len(rr.content) < 5000:
                raise ValueError("사진이 너무 작음")
            (out_dir / "_src.jpg").write_bytes(rr.content)
            Image.open(out_dir / "_src.jpg").convert("RGB").save(out_dir / "_src.jpg", "JPEG", quality=95)
            src = a.get("photo_src", "")
            credit = src or a["press"]  # 사진 설명에 적힌 출처만 (없으면 언론사명)
            ph = {"path": str(out_dir / "_src.jpg"), "credit": credit, "license": "",
                  "page": a["url"], "source": "", "kind": "기사 사진"}
            print(f"[기사 사진] {img}")
        except Exception as e:
            print(f"[기사 사진 실패] {e} → 무료 사진 검색")
            ph = None
    else:
        ph = None
    if not user_photo and not ph:
        names = [s.get("person", "")] + list(s.get("photo_queries", []))
        ph = photo.find_photo([n for i, n in enumerate(names) if n and n not in names[:i]],
                              s.get("scene_queries", []), s.get("tag", "연예"), out_dir / "_src.jpg")
    if not ph:
        return False, f"‘{s.get('person') or a['title']}’ 인물의 무료 사진을 못 찾아서 못 올렸어"
    s["issue_no"] = collect._next_issue()
    paths = render.render_story(s, ph, out_dir)
    (out_dir / "_src.jpg").unlink(missing_ok=True)
    record = {"id": post_id, "created": now.isoformat(), "status": "pending", "attempts": 0, "manual": True,
              "publish_at": PUBLISH_AT,
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


def make_capture_post(text: str, file_id: str, ch: dict) -> tuple[bool, str]:
    """링크 없이 SNS 캡처만 보냈을 때: 캡처 읽기 → 관련 뉴스로 사실 확인 → 긍정적인 소식 카드.
    캡처를 여러 장 보내면: 1장째 = 제목 카드, 나머지 = 사진만 (요약 카드 없음)."""
    now = datetime.now(KST)
    post_id = f"{now:%Y%m%d-%H%M%S}-c-{ch['key']}"
    out_dir = config.POSTS_DIR / post_id
    src = out_dir / "_src.jpg"
    ids = [f for f in file_id.split(",") if f][:10]  # 인스타 슬라이드 최대 10장
    extras = []
    try:
        for i, fid in enumerate(ids):
            dest = src if i == 0 else out_dir / f"_x{i}.jpg"
            telegram.download_file(fid, dest)
            Image.open(dest).convert("RGB").save(dest, "JPEG", quality=95)
            if i:
                extras.append(dest)
    except Exception as e:
        return False, f"보내준 캡처를 못 받았어: {e}"
    info = summarize.read_capture(str(src))
    if not info:
        return False, "캡처를 못 읽었어 (Claude 키·잔액 확인)"
    hint = URL_RE.sub(" ", text or "")
    for words in ALIASES.values():
        for w in words:
            hint = hint.replace(w, " ")
    hint = CREDIT_RE.sub(" ", hint).replace("캡처", " ").strip()
    if hint:  # 같이 적어 보낸 메모 (예: '리센느 마마 수상') → 검색·작성 힌트
        info["memo_from_sender"] = hint
    owner = photo.instagram_owner(info.get("account", ""))  # 위키데이터에 등록된 인스타 주인 (정확)
    if owner:
        info["person"] = owner
    print(f"[캡처 정보] {json.dumps(info, ensure_ascii=False)}")
    if not info.get("person") and hint:  # 보낸 메모의 첫 단어를 인물 이름으로 (예: '장원영 마마')
        info["person"] = hint.split()[0]
    if not (info.get("person") or info.get("account")):
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
        # 사진은 그대로 두고, 이 메시지에 '답장'으로 이름을 받으면 이어서 진행
        ask = telegram.send_message("🤔 사진만으로는 누군지 모르겠어.\n이 메시지에 '답장'으로 이름만 보내줘 (예: 장원영 / 장원영 마마 레드카펫)")
        config.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (config.PENDING_DIR / f"{post_id}-ask.json").write_text(json.dumps({
            "id": f"{post_id}-ask", "created": now.isoformat(), "status": "need_name", "manual": True, "images": [],
            "ask_msg": (ask or {}).get("message_id"), "file_ids": file_id, "text": text, "channel": ch["key"],
            "publish_at": PUBLISH_AT},
            ensure_ascii=False, indent=2))
        return True, "이름 물어봄"
    items, seen_t = [], set()
    person = info.get("person", "")
    for q in [hint, info.get("search_query"), person]:  # 여러 검색어 결과를 합침
        if not q:
            continue
        if person and person not in q:
            q = f"{person} {q}"
        for it in news.search_news(q):
            if it["title"] not in seen_t:
                seen_t.add(it["title"])
                items.append(it)
    items = items[:15]
    s = summarize.write_capture_story(info, items)
    if not s.get("ok"):
        return False, "글을 못 만들었어 (AI 응답 오류)"
    s["press"], s["url"], s["source_title"] = "", "", f"{info.get('account', '')} SNS"
    crop_ok = render.crop_capture(str(src))
    who = info.get("account") or info.get("person")
    m = CREDIT_RE.search(text or "")
    plat = {"instagram": "인스타그램", "youtube": "유튜브 캡처", "tiktok": "틱톡 캡처", "x": "X 캡처",
            "tv": "방송 캡처"}.get((info.get("platform") or "").lower(), "캡처")
    credit = m.group(1).strip() if m else f"{who} {plat}".strip()
    ph = {"path": str(src), "credit": credit, "license": "", "page": "", "source": "", "kind": "기사 사진"}
    s["issue_no"] = collect._next_issue()
    s["multi"] = bool(extras)
    paths = _render_capture(s, ph, out_dir)
    for i, x in enumerate(extras, 2):
        render.crop_capture(str(x))
        paths.append(render.photo_slide(str(x), out_dir / f"{i:02d}.jpg"))
        x.unlink(missing_ok=True)
    # _src.jpg(표지 원본)는 제목 수정 때 다시 쓰려고 승인·취소 전까지 보관
    # 사진 여러 장일 땐 캡션 = 제목 + 사진 출처 + 해시태그만 (본문 없음)
    caption = collect.build_caption(dict(s, body="") if extras else s, ph)
    record = {"id": post_id, "created": now.isoformat(), "status": "awaiting", "attempts": 0, "manual": True,
              "publish_at": PUBLISH_AT,
              "images": [str(p.relative_to(config.ROOT)) for p in paths],
              "caption": caption, "story": s, "photo": ph,
              "channel": ch["key"], "channel_handle": ch.get("handle", "")}
    config.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    _send_preview(record)
    note = "" if s.get("confirmed") else " (행사명은 뉴스로 확인 안 돼서 뺐어)"
    return True, "미리보기 보냄 — " + s.get("title", "") + note + ("" if crop_ok else " [캡처 자르기 실패 → 원본 그대로]")


def _render_capture(s: dict, ph: dict, out_dir) -> list:
    """표지(+한 장일 땐 요약 카드) 만들기. 여러 장이면 요약 카드 없이 표지만."""
    story = {k: v for k, v in s.items() if k not in ("body", "card_summary")} if s.get("multi") else s
    return render.render_story(story, ph, out_dir)


def _send_preview(record: dict, header: str = "이대로 올릴까요?"):
    """단톡방에 미리보기 + 버튼. 이 메시지에 '답장'으로 새 제목을 보내면 수정됨."""
    s, post_id = record["story"], record["id"]
    try:
        telegram.send_album([config.ROOT / p for p in record["images"]])
    except Exception as e:
        print(f"[미리보기 실패] {e}")
    warn = "" if s.get("confirmed") else "\n(⚠️ 수상·행사명은 뉴스로 확인이 안 돼서 뺐어)"
    if record.get("publish_at"):
        header += f"\n⏰ 승인하면 {_at_label(record['publish_at'])}에 올라가"
    tip = ("\n\n✏️ 고치려면 이 메시지에 '답장'으로:\n"
           "• 표지 제목 → 그냥 두 줄 (첫줄 흰 글씨 / 둘째줄 컬러)\n"
           "• 출처: 사진 출처\n• 캡션제목: 캡션 📌 첫 줄\n• 내용: 캡션 본문\n• 태그: #해시태그\n"
           "• 요청: 더 짧게 / 더 밝게 같은 부탁")
    msg = telegram.send_message(f"👀 [{record.get('channel_handle')}] {header}{warn}\n\n{record['caption'][:2800]}{tip}",
                                buttons=[[{"text": "✅ 올리기", "callback_data": f"ok:{post_id}"},
                                          {"text": "❌ 취소", "callback_data": f"no:{post_id}"}]])
    record["preview_msg"] = (msg or {}).get("message_id")
    (config.PENDING_DIR / f"{post_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))


def handle_edit(reply_to: str, text: str):
    """미리보기 메시지에 답장으로 온 새 제목으로 표지를 다시 만듦."""
    rec_path = rec = None
    for p in config.PENDING_DIR.glob("*.json"):
        r = json.loads(p.read_text())
        if r.get("status") == "need_name" and str(r.get("ask_msg")) == str(reply_to):
            # 이름 물어본 메시지에 답장 → 아까 사진들로 이어서 기사 쓰기
            p.unlink()
            global PUBLISH_AT
            PUBLISH_AT = r.get("publish_at", "")
            ch = next((c for c in config.channels() if c["key"] == r.get("channel")), None) or pick_channel(r.get("text", ""))
            memo = f"{r.get('text', '')} {text.strip()}".replace("캡처", " ").strip()
            telegram.send_message(f"🛠 {text.strip()[:20]}(으)로 다시 쓰는 중… [{ch.get('handle')}]")
            try:
                ok, msg = make_capture_post(memo, r["file_ids"], ch)
            except Exception as e:
                ok, msg = False, f"처리 중 오류: {str(e)[:200]}"
            print(("[준비 완료] " if ok else "[실패] ") + msg)
            if not ok:
                telegram.send_message(f"⚠️ {msg}")
            return
        if str(r.get("preview_msg")) == str(reply_to):
            rec_path, rec = p, r
            break
    if not rec:
        return  # 미리보기가 아닌 메시지에 단 답장 → 무시
    if rec.get("status") != "awaiting":
        telegram.send_message(f"ℹ️ 이미 처리된 카드라 수정 못 해 ({rec.get('status')})")
        return
    fields = parse_edit(text)
    if not fields:
        return
    s = rec["story"]
    changed = []
    if fields.get("request"):  # '요청: 더 짧게' 같은 자유 요청 → AI가 고쳐 씀
        rev = summarize.revise_story(s, fields["request"])
        for k in ("headline_top", "headline_bottom", "title", "body", "hashtags"):
            if rev.get(k):
                s[k] = rev[k]
        changed.append("요청 반영")
    if fields.get("lines"):
        s["headline_top"] = fields["lines"][0]
        if len(fields["lines"]) > 1:
            s["headline_bottom"] = fields["lines"][1]
        changed.append("표지 제목")
    if fields.get("title"):
        s["title"] = fields["title"]; changed.append("캡션 제목")
    if fields.get("body") is not None:
        s["body"] = fields["body"]; s["body_custom"] = True; changed.append("내용")
    if fields.get("tags"):
        s["hashtags"] = ["#" + t.lstrip("#") for t in re.split(r"[\s,]+", fields["tags"]) if t.strip("#")]
        changed.append("해시태그")
    if fields.get("credit") is not None:
        rec["photo"]["credit"] = fields["credit"]; changed.append("사진 출처")
    out_dir = (config.ROOT / rec["images"][0]).parent
    src = out_dir / "_src.jpg"
    if not src.exists():
        telegram.send_message("⚠️ 원본 사진이 없어서 다시 못 만들었어. 캡처를 다시 보내줘")
        return
    ph = dict(rec["photo"], path=str(src))
    _render_capture(s, ph, out_dir)  # 01.jpg(+02.jpg) 다시 그림, 나머지 사진 장은 그대로
    # 캡션 다시 만들기 (여러 장이면 본문은 직접 써준 경우에만)
    cap_story = s if (not s.get("multi") or s.get("body_custom")) else dict(s, body="")
    rec["caption"] = collect.build_caption(cap_story, rec["photo"])
    if rec.get("preview_msg"):
        telegram.edit_buttons(rec["preview_msg"], "✏️ 수정됨 (아래 새 미리보기)")
    rec["story"] = s
    _send_preview(rec, f"{', '.join(changed)} 바꿨어! 이대로 올릴까요?")
    print(f"[수정] {rec['id']}: {changed}")


EDIT_KEYS = {"제목": "cover", "표지": "cover", "캡션제목": "title", "내용": "body", "본문": "body", "출처": "credit",
             "사진출처": "credit", "사진": "credit", "태그": "tags", "해시태그": "tags", "요청": "request"}


def parse_edit(text: str) -> dict:
    """답장 내용 해석.
    '출처: OO' / '내용: ...'(여러 줄 가능) / '제목: ...'(캡션 첫 줄) / '태그: #a #b' / '요청: 더 짧게'
    접두어 없는 줄 = 표지 제목 (첫 줄 흰 글씨, 둘째 줄 컬러 글씨, 또는 '첫줄 / 둘째줄')."""
    out, cur, plain = {}, None, []
    for raw in text.strip().splitlines():
        m = re.match(r"\s*(캡션\s*제목|제목|표지|내용|본문|사진\s*출처|출처|사진|해시태그|태그|요청)\s*[:：]\s*(.*)$", raw)
        if m:
            cur = EDIT_KEYS[m.group(1).replace(" ", "")]
            out[cur] = m.group(2).strip()
        elif cur == "body":  # 내용은 여러 줄 이어서
            out["body"] = (out["body"] + "\n" + raw.strip()).strip() if raw.strip() else out["body"] + "\n"
        elif raw.strip():
            cur = None
            plain.append(raw.strip())
    if out.get("cover"):  # '제목: 첫줄 / 둘째줄' = 표지 제목
        plain = [x.strip() for x in out.pop("cover").split("/", 1)] + plain
    if "body" in out:
        out["body"] = re.sub(r"\n{3,}", "\n\n", out["body"]).strip()
    if len(plain) == 1 and "/" in plain[0]:
        plain = [x.strip() for x in plain[0].split("/", 1)]
    if plain:
        out["lines"] = plain[:2]
    return out


def handle_approval(args: list[str]):
    """args = ['ok:<post_id>', '<message_id>'] → 승인이면 업로드 대기열로, 취소면 삭제."""
    if not args or ":" not in args[0]:
        return
    act, post_id = args[0].split(":", 1)
    msg_id = args[1] if len(args) > 1 else None
    rec_path = config.PENDING_DIR / f"{post_id}.json"
    if not re.fullmatch(r"[\w-]+", post_id) or not rec_path.exists():
        telegram.send_message("⚠️ 이미 처리됐거나 기한(2일)이 지난 카드야")
        return
    rec = json.loads(rec_path.read_text())
    if rec.get("status") != "awaiting":
        telegram.send_message(f"ℹ️ 이미 처리된 카드야 ({rec.get('status')})")
        return
    post_dir = (config.ROOT / rec["images"][0]).parent if rec.get("images") else None
    if post_dir:
        (post_dir / "_src.jpg").unlink(missing_ok=True)
    if act == "ok":
        rec["status"] = "pending"
        rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
        if msg_id:
            telegram.edit_buttons(msg_id, "✅ 승인됨 — 업로드 중")
        print(f"[승인] {post_id}")
    else:
        for img in rec.get("images", []):
            (config.ROOT / img).unlink(missing_ok=True)
        rec_path.unlink()
        if msg_id:
            telegram.edit_buttons(msg_id, "❌ 취소됨")
        print(f"[취소] {post_id}")


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
    global PUBLISH_AT
    tg_text, tg_file = os.environ.get("TG_TEXT", ""), os.environ.get("TG_FILE_ID", "")
    if tg_text.startswith("PUBLISH_DUE"):  # 예약 시간이 된 게시물 올리기 (업로드는 다음 단계에서)
        print("예약 시간 도착 → 업로드 단계로")
        return
    m_at = re.match(r"AT=(\S+)\s*", tg_text)
    if m_at:
        PUBLISH_AT, tg_text = m_at.group(1), tg_text[m_at.end():] or "캡처"
        print(f"[예약] {PUBLISH_AT}")
    if tg_text.startswith("APPROVE "):  # 미리보기 버튼 (워커가 보내줌)
        handle_approval(tg_text.split()[1:])
        return
    if tg_text.startswith("EDIT "):  # 미리보기에 답장으로 보낸 새 제목
        head, _, body = tg_text.partition("\n")
        handle_edit(head.split()[1] if len(head.split()) > 1 else "", body)
        return
    if tg_text or tg_file:
        up = None
        if tg_file:
            m = CREDIT_RE.search(tg_text)
            up = {"file_id": tg_file, "credit": m.group(1).strip() if m else ""}
        urls += [(u, tg_text, up) for u in URL_RE.findall(tg_text)]
    # 링크 없이 캡처만 보낸 경우 → 캡처로 소식 쓰기 (채널 이름을 같이 써야 함)
    if tg_file and not URL_RE.search(tg_text or ""):
        ch = pick_channel(tg_text)  # 채널 이름 없으면 기본 채널(입덕)
        telegram.send_message(f"🛠 캡처 접수! [{ch.get('handle')}] 내용 읽고 기사 쓰는 중…"
                              + (f" (⏰ {_at_label(PUBLISH_AT)} 예약)" if PUBLISH_AT else ""))
        try:
            ok, msg = make_capture_post(tg_text, tg_file, ch)
        except Exception as e:
            ok, msg = False, f"처리 중 오류: {str(e)[:200]}"
        print(("[준비 완료] " if ok else "[실패] ") + msg)
        if not ok:
            telegram.send_message(f"⚠️ {msg}")
        return
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
        telegram.send_message(f"🛠 접수! [{ch.get('handle')}] 카드 만드는 중…" + (" (보낸 사진 사용)" if up else "")
                              + (f" (⏰ {_at_label(PUBLISH_AT)} 예약)" if PUBLISH_AT else "") + f"\n{url}")
        try:
            ok, msg = make_post(url, ch, up)
            if ok and PUBLISH_AT:
                telegram.send_message(f"⏰ 카드 준비 완료! {_at_label(PUBLISH_AT)}에 [{ch.get('handle')}]에 올라가\n{msg}")
        except Exception as e:  # Gemini 전체 혼잡 등 → 조용히 죽지 말고 단톡방에 알림
            ok = False
            msg = ("AI(Gemini) 서버가 지금 전부 혼잡해서 요약을 못 만들었어. 10~30분 뒤에 링크를 다시 보내줘"
                   if "Gemini" in str(e) else f"처리 중 오류: {str(e)[:200]}")
        print(("[준비 완료] " if ok else "[실패] ") + msg)
        if not ok:
            telegram.send_message(f"⚠️ {msg}\n{url}")


if __name__ == "__main__":
    main()
