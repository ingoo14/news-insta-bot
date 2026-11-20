"""10분마다 실행: 텔레그램 버튼 응답 확인 → 승인된 건 인스타 업로드."""
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone

from . import config, instagram, telegram


def image_url(rel_path: str, sha: str) -> str:
    repo = config.GITHUB_REPOSITORY
    if os.environ.get("IMAGE_HOST", "jsdelivr") == "raw":
        return f"https://raw.githubusercontent.com/{repo}/{sha}/{rel_path}"
    return f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{rel_path}"


def head_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()


def load(post_id):
    p = config.PENDING_DIR / f"{post_id}.json"
    return (json.loads(p.read_text()), p) if p.exists() else (None, p)


def finish(rec, path, status):
    """처리 끝난 건은 이미지 삭제(저장소 용량 관리)하고 상태 기록."""
    rec["status"] = status
    rec["done"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    shutil.rmtree(config.POSTS_DIR / rec["id"], ignore_errors=True)


def handle(cb):
    data = cb.get("data", "")
    chat_id = str(cb.get("message", {}).get("chat", {}).get("id"))
    if chat_id != str(config.TELEGRAM_CHAT_ID) or ":" not in data:
        return
    action, post_id = data.split(":", 1)
    rec, path = load(post_id)
    if not rec or rec["status"] != "pending":
        telegram.answer_callback(cb["id"], "이미 처리됐거나 만료된 건")
        return
    msg_id = rec.get("tg_message_id")

    if action == "no":
        finish(rec, path, "rejected")
        telegram.answer_callback(cb["id"], "버림")
        telegram.edit_buttons(msg_id, "❌ 버림")
        return

    if action == "ok":
        telegram.answer_callback(cb["id"], "업로드 시작")
        sha = head_sha()
        urls = [image_url(p, sha) for p in rec["images"]]
        try:
            link = instagram.publish_carousel(urls, rec["caption"])
        except Exception as e:
            telegram.send_message(f"⚠️ [{post_id}] 업로드 실패 — 버튼 다시 누르면 재시도\n{e}", reply_to=msg_id)
            return
        rec["permalink"] = link
        finish(rec, path, "published")
        telegram.edit_buttons(msg_id, "✅ 업로드 완료")
        telegram.send_message(f"✅ [{post_id}] 인스타 업로드 완료\n{link}", reply_to=msg_id)


def expire_old():
    limit = datetime.now(timezone.utc) - timedelta(hours=config.PENDING_EXPIRE_HOURS)
    for p in config.PENDING_DIR.glob("*.json"):
        rec = json.loads(p.read_text())
        if rec["status"] == "pending" and datetime.fromisoformat(rec["created"]) < limit:
            finish(rec, p, "expired")
            telegram.edit_buttons(rec.get("tg_message_id"), "⌛ 만료됨")
        elif rec["status"] != "pending" and datetime.fromisoformat(rec.get("done", rec["created"])) < limit - timedelta(days=6):
            p.unlink()  # 처리 끝난 기록은 일주일 뒤 정리


def maybe_refresh_token():
    """(인스타 로그인 방식 토큰일 때만) 60일 만료 → 7일마다 자동 연장. 시스템 사용자 토큰은 건너뜀."""
    if not config.IG_TOKEN_REFRESH:
        return
    f = config.TOKEN_CHECK_FILE
    last = date.fromisoformat(f.read_text().strip()) if f.exists() else date.min
    if (date.today() - last).days < 7:
        return
    try:
        new = instagram.refresh_token()
    except Exception as e:
        telegram.send_message(f"⚠️ 인스타 토큰 연장 실패 — 토큰 재발급 필요할 수 있음\n{e}")
        return
    f.write_text(date.today().isoformat())
    if new != config.IG_ACCESS_TOKEN:
        if os.environ.get("GH_PAT"):
            subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--body", new, "--repo", config.GITHUB_REPOSITORY],
                           env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]}, check=True)
        else:
            telegram.send_message("🔑 인스타 토큰이 새로 발급됨. GitHub Secrets의 IG_ACCESS_TOKEN을 아래 값으로 바꿔줘:\n" + new)


def main():
    for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "IG_USER_ID", "IG_ACCESS_TOKEN"]:
        config.env(k, required=True)
    offset = int(config.OFFSET_FILE.read_text().strip() or 0) if config.OFFSET_FILE.exists() else 0
    updates = telegram.get_updates(offset + 1 if offset else 0)
    for u in updates:
        offset = max(offset, u["update_id"])
        if "callback_query" in u:
            handle(u["callback_query"])
        elif u.get("message", {}).get("text", "").strip() == "/id":
            # 처음 세팅할 때 chat id 확인용
            print("chat id:", u["message"]["chat"]["id"])
    config.OFFSET_FILE.write_text(str(offset))
    expire_old()
    maybe_refresh_token()


if __name__ == "__main__":
    main()
