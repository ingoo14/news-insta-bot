"""2시간마다 2단계: 대기열의 카드를 인스타에 자동 업로드 → 텔레그램으로 완료 알림."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

from . import config, instagram, telegram

MAX_ATTEMPTS = 3


def head_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()


def image_urls(rec, sha, host):
    repo = config.GITHUB_REPOSITORY
    if host == "raw":
        return [f"https://raw.githubusercontent.com/{repo}/{sha}/{p}" for p in rec["images"]]
    return [f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{p}" for p in rec["images"]]


def notify(text):
    try:
        telegram.send_message(text)
    except Exception as e:
        print(f"[텔레그램 실패] {e}")


def main():
    for k in ["IG_USER_ID", "IG_ACCESS_TOKEN"]:
        config.env(k, required=True)
    sha = head_sha()
    first = os.environ.get("IMAGE_HOST") or "jsdelivr"
    hosts = [first] + [h for h in ("jsdelivr", "raw") if h != first]
    kind = (os.environ.get("PUBLISH_KIND") or "all").lower()  # auto / manual / all
    for p in sorted(config.PENDING_DIR.glob("*.json")):
        rec = json.loads(p.read_text())
        if rec.get("status") != "pending":
            continue
        if kind == "auto" and rec.get("manual") or kind == "manual" and not rec.get("manual"):
            continue
        rec["attempts"] = rec.get("attempts", 0) + 1
        title = rec.get("story", {}).get("title", rec["id"])
        link, err = None, None
        for host in hosts:  # CDN이 안 되면 GitHub 원본 주소로 재시도
            try:
                link = instagram.publish(image_urls(rec, sha, host), rec["caption"])
                break
            except Exception as e:
                err = e
                print(f"[업로드 실패/{host}] {rec['id']}: {e}")
        if link is not None:
            rec["status"], rec["permalink"] = "published", link
            shutil.rmtree(config.POSTS_DIR / rec["id"], ignore_errors=True)
            ph = rec.get("photo") or {}
            notify(f"✅ 인스타 업로드 완료\n{title}\n{link}\n사진: {ph.get('kind', '')} / {ph.get('page', '')}")
        elif rec["attempts"] >= MAX_ATTEMPTS:
            rec["status"] = "failed"
            shutil.rmtree(config.POSTS_DIR / rec["id"], ignore_errors=True)
            notify(f"⚠️ 업로드 {MAX_ATTEMPTS}번 실패해서 포기함\n{title}\n{err}")
        else:
            notify(f"⚠️ 업로드 실패 (다음 회차에 재시도)\n{title}\n{err}")
        rec["done"] = datetime.now(timezone.utc).isoformat()
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2))

    # 처리 끝난 기록은 2일 뒤 정리
    now = datetime.now(timezone.utc)
    for p in config.PENDING_DIR.glob("*.json"):
        rec = json.loads(p.read_text())
        if rec.get("status") != "pending" and (now - datetime.fromisoformat(rec.get("done", rec["created"]))).days >= 2:
            p.unlink()


if __name__ == "__main__":
    main()
