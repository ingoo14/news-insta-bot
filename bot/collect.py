"""6시간마다 실행: 뉴스 수집 → 선별·요약 → 카드뉴스 생성 → 텔레그램 승인 요청."""
import json
from datetime import datetime, timedelta, timezone

from . import config, news, render, summarize, telegram

KST = timezone(timedelta(hours=9))


def load_seen() -> list[str]:
    try:
        return json.loads(config.SEEN_FILE.read_text())
    except Exception:
        return []


def main():
    for k in ["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        config.env(k, required=True)

    seen = load_seen()
    cands = news.collect_candidates(set(seen))
    print(f"후보 기사 {len(cands)}건")
    if len(cands) < 2:
        print("새 기사가 부족해서 이번 회차는 건너뜀")
        return

    n = config.ITEMS_PER_POST
    picks = summarize.pick(cands, n + 1)  # 하나 여유 있게 (본문 부실하면 빠짐)
    chosen = [cands[i] for i in picks]
    for c in chosen:
        c["body"] = news.fetch_body(c)
    post = summarize.write_post(chosen)
    post["items"] = post["items"][:n]
    if not post["items"]:
        telegram.send_message("⚠️ 이번 회차는 올릴 만한 기사가 없어서 건너뜀")
        return

    now = datetime.now(KST)
    post_id = now.strftime("%Y%m%d-%H%M")
    paths = render.render_post(post, post_id, now)

    tags = " ".join(post.get("hashtags", [])[:15])
    caption = f"{post.get('caption', '').strip()}\n\n{tags}".strip()
    record = {
        "id": post_id, "created": now.isoformat(), "status": "pending",
        "images": [str(p.relative_to(config.ROOT)) for p in paths],
        "caption": caption, "items": post["items"],
    }

    telegram.send_album(paths)
    src = "\n".join(f"{i}. {it['headline']} ({it['press']})\n   {it['url']}" for i, it in enumerate(post["items"], 1))
    msg = telegram.send_message(
        f"📰 [{post_id}] 카드뉴스 승인 요청\n\n{src}\n\n— 캡션 미리보기 —\n{caption}",
        buttons=[[{"text": "✅ 인스타 업로드", "callback_data": f"ok:{post_id}"},
                  {"text": "❌ 버리기", "callback_data": f"no:{post_id}"}]],
    )
    record["tg_message_id"] = msg["message_id"]

    config.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    (config.PENDING_DIR / f"{post_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    seen = (seen + [c["url"] for c in cands])[-3000:]  # 이번에 본 기사는 다음 회차에서 제외
    config.SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False))
    print(f"승인 요청 전송 완료: {post_id}")


if __name__ == "__main__":
    main()
