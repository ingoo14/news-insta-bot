"""2시간마다 1단계: 뉴스 수집 → 2건 선별·요약 → 사진 카드 생성 → 업로드 대기열(state/pending)에 저장.
(이미지를 저장소에 올린 뒤 2단계 bot.publish 가 인스타에 자동 업로드)"""
import json
from datetime import datetime, timedelta, timezone

from . import config, news, photo, render, summarize

KST = timezone(timedelta(hours=9))


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def build_caption(s: dict, ph: dict) -> str:
    tags = " ".join(s.get("hashtags", [])[:15])
    return (f"📌 {s.get('title', '').strip()}\n\n{s.get('body', '').strip()}\n\n"
            f"기사 출처: {s['press']}\n"
            f"사진: {ph['credit']} / {ph['license']} / {ph['source']}\n\n{tags}").strip()


def main():
    config.env("GEMINI_API_KEY", required=True)
    seen = _load(config.SEEN_FILE, [])
    topics = _load(config.TOPICS_FILE, [])
    cands = news.collect_candidates(set(seen))
    print(f"후보 기사 {len(cands)}건")
    if not cands:
        print("새 기사가 없어서 이번 회차는 건너뜀")
        return

    want = config.POSTS_PER_RUN
    picks = summarize.pick(cands, want + 3, topics)
    now = datetime.now(KST)
    made = 0
    for i in picks:
        if made >= want:
            break
        a = cands[i]
        seen.append(a["url"])
        a["body"] = news.fetch_body(a)
        s = summarize.write_story(a)
        if not (s.get("ok") and s.get("headline_top") and s.get("body")):
            print(f"[건너뜀] 내용 부실/원칙 위반: {a['title']}")
            continue
        post_id = f"{now:%Y%m%d-%H%M}-{made + 1}"
        out_dir = config.POSTS_DIR / post_id
        ph = photo.find_photo(s.get("photo_queries", []), s.get("scene_queries", []), s.get("tag", "연예"),
                              out_dir / "_src.jpg")
        if not ph:  # 사진은 반드시 들어가야 함 → 못 찾으면 다음 기사로
            print(f"[건너뜀] 사진 못 찾음: {a['title']}")
            continue
        paths = render.render_story(s, ph, out_dir)
        (out_dir / "_src.jpg").unlink(missing_ok=True)
        record = {
            "id": post_id, "created": now.isoformat(), "status": "pending", "attempts": 0,
            "images": [str(p.relative_to(config.ROOT)) for p in paths],
            "caption": build_caption(s, ph), "story": s, "photo": ph,
        }
        config.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (config.PENDING_DIR / f"{post_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        topics.append(f"{s.get('title', '')} ({now:%m/%d})")
        made += 1
        print(f"[준비 완료] {post_id}: {s.get('title')}")

    config.SEEN_FILE.write_text(json.dumps(seen[-3000:], ensure_ascii=False))
    config.TOPICS_FILE.write_text(json.dumps(topics[-60:], ensure_ascii=False, indent=1))
    print(f"이번 회차 {made}건 준비")


if __name__ == "__main__":
    main()
