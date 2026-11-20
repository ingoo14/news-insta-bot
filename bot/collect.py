"""2시간마다 1단계: 뉴스 수집 → 2건 선별·요약 → 사진 카드 생성 → 업로드 대기열(state/pending)에 저장.
(이미지를 저장소에 올린 뒤 2단계 bot.publish 가 인스타에 자동 업로드)"""
import json
from datetime import datetime, timedelta, timezone

from . import config, news, photo, render, summarize, trends

KST = timezone(timedelta(hours=9))


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def diversify(cands: list[dict], per_press: int = 6) -> list[dict]:
    """한 언론사가 후보를 독점하지 않게: 점수 순서는 유지하되 언론사별로 번갈아 섞고, 언론사당 최대 N건."""
    from collections import Counter, defaultdict
    groups = defaultdict(list)
    for c in cands:
        groups[c["press"]].append(c)
    stat = Counter(c["press"] for c in cands)
    print("[언론사별 후보] " + ", ".join(
        f"{p} {n}건(최신 {min(x.get('age_min', 0) for x in groups[p])}분 전)" for p, n in stat.most_common()))
    out, rnd = [], 0
    while len(out) < len(cands) and rnd < per_press:
        for p in sorted(groups, key=lambda p: -groups[p][0].get("score", 0) if groups[p] else 0):
            if len(groups[p]) > rnd:
                out.append(groups[p][rnd])
        rnd += 1
    return out


def _next_issue() -> int:
    f = config.STATE_DIR / "issue_no.txt"
    try:
        n = int(f.read_text().strip()) + 1
    except Exception:
        n = 1
    f.write_text(str(n))
    return n


def build_caption(s: dict, ph: dict) -> str:
    tags = " ".join(s.get("hashtags", [])[:15])
    credit = " / ".join(x for x in (ph.get("credit"), ph.get("license"), ph.get("source")) if x)
    return (f"📌 {s.get('title', '').strip()}\n\n{s.get('body', '').strip()}\n\n"
            f"기사 출처: {s['press']}\n" + (f"사진: {credit}\n" if credit else "") + f"\n{tags}").strip()


def main():
    config.env("GEMINI_API_KEY", required=True)
    seen = _load(config.SEEN_FILE, [])
    topics = _load(config.TOPICS_FILE, [])
    cands = news.collect_candidates(set(seen))
    tr = trends.collect()
    known = {c["url"] for c in cands} | set(seen)
    cands += [c for c in trends.trend_news_candidates(tr) if c["url"] not in known]
    trends.score_candidates(cands, tr)
    hot = trends.hot_keywords(cands)
    trends.score_freshness(cands, hot)
    # 종합 점수(실시간성 + 음악 우선 + 실시간 이벤트 + 구글/유튜브 트렌드) 높은 순
    cands.sort(key=lambda c: (c.get("score", 0), c["published"]), reverse=True)
    cands = diversify(cands)
    print(f"후보 기사 {len(cands)}건 / 실시간 이벤트 키워드: {hot} / 트렌드 일치 {sum(1 for c in cands if c.get('trend_score'))}건")
    if not cands:
        print("새 기사가 없어서 이번 회차는 건너뜀")
        return

    chans = config.channels() or [{"key": "ch1", "handle": "메인", "posts": config.POSTS_PER_RUN}]
    slots = [c for c in chans for _ in range(c["posts"])]  # 예: [메인, 메인, 채널2, 채널3]
    want = len(slots)
    live = ", ".join(f"{w}({n}건)" for w, n in hot) or "(없음)"
    picks = summarize.pick(cands, want + 6, topics, trends.summary_for_prompt(tr), live)
    now = datetime.now(KST)
    made = 0
    for i in picks:
        if made >= want:
            break
        a = cands[i]
        seen.append(a["url"])
        a["body"] = news.fetch_body(a)
        s = summarize.write_story(a)
        s["trend_label"] = a.get("trend_label", "")
        if not (s.get("ok") and s.get("headline_top") and s.get("body")):
            print(f"[건너뜀] 내용 부실/원칙 위반: {a['title']}")
            continue
        ch = slots[made]
        post_id = f"{now:%Y%m%d-%H%M}-{made + 1}-{ch['key']}"
        out_dir = config.POSTS_DIR / post_id
        names = [s.get("person", "")] + list(s.get("photo_queries", []))
        ph = photo.find_photo([n for i, n in enumerate(names) if n and n not in names[:i]],
                              s.get("scene_queries", []), s.get("tag", "연예"),
                              out_dir / "_src.jpg")
        if not ph:  # 사진은 반드시 들어가야 함 → 못 찾으면 다음 기사로
            print(f"[건너뜀] 사진 못 찾음: {a['title']}")
            continue
        s["issue_no"] = _next_issue()
        paths = render.render_story(s, ph, out_dir)
        (out_dir / "_src.jpg").unlink(missing_ok=True)
        record = {
            "id": post_id, "created": now.isoformat(), "status": "pending", "attempts": 0,
            "images": [str(p.relative_to(config.ROOT)) for p in paths],
            "caption": build_caption(s, ph), "story": s, "photo": ph,
            "channel": ch["key"], "channel_handle": ch.get("handle", ""),
        }
        config.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (config.PENDING_DIR / f"{post_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        topics.append(f"{s.get('title', '')} ({now:%m/%d})")
        made += 1
        print(f"[준비 완료] {post_id} → {ch.get('handle')}: {s.get('title')}")

    config.SEEN_FILE.write_text(json.dumps(seen[-3000:], ensure_ascii=False))
    config.TOPICS_FILE.write_text(json.dumps(topics[-60:], ensure_ascii=False, indent=1))
    print(f"이번 회차 {made}건 준비")


if __name__ == "__main__":
    main()
