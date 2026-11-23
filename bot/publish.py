"""2시간마다 2단계: 대기열의 카드를 인스타에 자동 업로드 → 텔레그램으로 완료 알림."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta

from . import config, instagram, telegram

MAX_ATTEMPTS = 3


def head_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()


def image_urls(rec, sha, host):
    repo = config.GITHUB_REPOSITORY
    if host == "raw":
        return [f"https://raw.githubusercontent.com/{repo}/{sha}/{p}" for p in rec["images"]]
    return [f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{p}" for p in rec["images"]]


def _git(*args) -> bool:
    return subprocess.run(["git", *args], cwd=config.ROOT, capture_output=True, text=True).returncode == 0


def git_save(msg: str) -> bool:
    """지금 상태를 커밋하고 올림 (다른 실행과 겹치면 받아서 합친 뒤 재시도)."""
    _git("add", "-A", "--", ".", ":!fonts")
    if _git("diff", "--cached", "--quiet"):
        return True
    _git("commit", "-q", "-m", msg)
    for _ in range(5):
        if _git("push", "-q"):
            return True
        _git("pull", "--rebase", "-X", "theirs", "-q")
    return False


DONE_FILE = config.ROOT / "state" / "published_urls.json"  # 채널별로 이미 올린 기사 주소 (중복 업로드 방지)


def url_key(channel: str, url: str) -> str:
    """같은 기사 판별용: 주소에서 ?utm_… 같은 꼬리 제거."""
    u = (url or "").split("#")[0].split("?")[0].rstrip("/").lower()
    u = u.replace("://m.", "://").replace("://www.", "://")
    return f"{channel}|{u}" if u else ""


def load_done() -> dict:
    try:
        done = json.loads(DONE_FILE.read_text())
    except Exception:
        done = {}
    for p in config.PENDING_DIR.glob("*.json"):  # 최근 올린 기록도 같이 (파일 생기기 전에 올린 것 포함)
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        k = url_key(r.get("channel", ""), rec_url(r))
        if r.get("status") == "published" and k:
            done.setdefault(k, r.get("done", r.get("created", "")))
    return done


def rec_url(rec: dict) -> str:
    return rec.get("src_url") or (rec.get("story") or {}).get("url") or ""


def claim(p, rec_id: str):
    """중복 업로드 방지 잠금: 최신 상태를 받아서 아직 'pending'인지 확인 → 'publishing'으로 바꿔서 먼저 올린 쪽만 업로드.
    동시에 돈 다른 실행이 이미 가져갔으면 None."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return json.loads(p.read_text())
    for _ in range(4):
        _git("fetch", "-q", "origin")
        _git("reset", "-q", "--hard", "origin/HEAD") or _git("reset", "-q", "--hard", "origin/main")
        if not p.exists():
            return None
        rec = json.loads(p.read_text())
        if rec.get("status") != "pending":
            print(f"[이미 다른 실행이 처리 중/완료 → 건너뜀] {rec_id} ({rec.get('status')})")
            return None
        key = url_key(rec.get("channel", ""), rec_url(rec))
        busy = False  # 같은 기사를 다른 실행이 지금 올리는 중인지
        for q in config.PENDING_DIR.glob("*.json"):
            if q != p and key:
                try:
                    o = json.loads(q.read_text())
                except Exception:
                    continue
                if o.get("status") in ("publishing", "published") and url_key(o.get("channel", ""), rec_url(o)) == key:
                    busy = True
        if key and (key in load_done() or busy) and not rec.get("force"):  # 같은 채널에 같은 기사가 이미 올라감 → 절대 다시 안 올림
            rec["status"], rec["done"] = "duplicate", datetime.now(timezone.utc).isoformat()
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
            git_save(f"duplicate {rec_id}")
            notify(f"🚫 [{rec.get('channel_handle', '')}] 이미 올린 기사라 건너뜀\n{(rec.get('story') or {}).get('title', '')}")
            return None
        rec["status"] = "publishing"
        rec["claimed"] = datetime.now(timezone.utc).isoformat()
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
        _git("add", str(p))
        _git("commit", "-q", "-m", f"claim {rec_id}")
        if _git("push", "-q"):
            rec["status"] = "pending"
            return rec
        print(f"[잠금 충돌 → 다시 확인] {rec_id}")
    return None


def targets():
    """이번 실행이 올려야 할 게시물 id (manual.py가 남김). 파일이 없으면 None = 전부(예전 방식)."""
    f = os.environ.get("PUBLISH_IDS_FILE", "/tmp/publish_ids.json")
    try:
        return json.load(open(f))
    except Exception:
        return None


def notify(text):
    try:
        telegram.send_message(text)
    except Exception as e:
        print(f"[텔레그램 실패] {e}")


def main():
    chans = {c["key"]: c for c in config.channels()}
    if not chans:
        raise SystemExit("[설정 누락] IG_USER_ID / IG_ACCESS_TOKEN 확인")
    sha = head_sha()
    first = os.environ.get("IMAGE_HOST") or "jsdelivr"
    hosts = [first] + [h for h in ("jsdelivr", "raw") if h != first]
    kind = (os.environ.get("PUBLISH_KIND") or "all").lower()  # auto / manual / all
    want = targets()  # ['id', ...] 또는 {'due': true}
    if want is not None:
        print(f"[이번 실행 업로드 대상] {want}")
    for p in sorted(config.PENDING_DIR.glob("*.json")):
        rec = json.loads(p.read_text())
        if rec.get("status") != "pending":
            continue
        # 동시에 여러 링크를 보내도 각 실행은 '자기가 만든 게시물'만 올림 (예약은 시간이 된 것만)
        if isinstance(want, list) and rec["id"] not in want:
            continue
        if isinstance(want, dict) and want.get("due") and not rec.get("publish_at"):
            continue
        if kind == "auto" and rec.get("manual") or kind == "manual" and not rec.get("manual"):
            continue
        if rec.get("publish_at"):  # 예약 게시물: 시간이 안 됐으면 다음에
            try:
                at = datetime.fromisoformat(rec["publish_at"])
                if datetime.now(timezone.utc) < at - timedelta(minutes=3):
                    print(f"[예약 대기] {rec['id']} → {rec['publish_at']}")
                    continue
            except Exception as e:
                print(f"[예약 시각 오류 → 바로 올림] {e}")
        ch = chans.get(rec.get("channel", "ch1"))
        if not ch:
            print(f"[채널 설정 없음 → 건너뜀] {rec['id']} ({rec.get('channel')})")
            continue
        rec = claim(p, rec["id"])  # 잠금 (다른 실행이 이미 올렸으면 건너뜀)
        if rec is None:
            continue
        config.IG_USER_ID, config.IG_ACCESS_TOKEN = ch["user_id"], ch["token"]  # 이 게시물의 계정으로 전환
        rec["attempts"] = rec.get("attempts", 0) + 1
        title = rec.get("story", {}).get("title", rec["id"])
        link, err = None, None
        for host in hosts:  # CDN이 안 되면 GitHub 원본 주소로 재시도
            try:
                if rec.get("type") == "reel":  # 릴스: 영상은 용량 때문에 GitHub 원본 주소로
                    repo = config.GITHUB_REPOSITORY
                    vurl = f"https://raw.githubusercontent.com/{repo}/{sha}/{rec['video']}"
                    link = instagram.publish_reel(vurl, rec["caption"], rec.get("poll"))
                else:
                    link = instagram.publish(image_urls(rec, sha, host), rec["caption"], rec.get("poll"))
                break
            except Exception as e:
                err = e
                print(f"[업로드 실패/{host}] {rec['id']}: {e}")
        if link is not None:
            rec["status"], rec["permalink"] = "published", link
            key = url_key(rec.get("channel", ""), rec_url(rec))
            if key:
                done = load_done()
                done[key] = datetime.now(timezone.utc).isoformat()
                # 30일 지난 기록은 정리
                cut = datetime.now(timezone.utc) - timedelta(days=30)
                done = {k: v for k, v in done.items() if datetime.fromisoformat(v) > cut}
                DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
                DONE_FILE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            shutil.rmtree(config.POSTS_DIR / rec["id"], ignore_errors=True)
            ph = rec.get("photo") or {}
            poll_txt = ""
            if rec.get("poll"):
                poll_txt = f"\n⚠️ {instagram.POLL_NOTE}" if instagram.POLL_NOTE else "\n🗳 투표: " + " / ".join(rec["poll"])
            notify(f"✅ [{ch['handle']}] 업로드 완료\n{title}\n{link}\n사진: {ph.get('page', '')}{poll_txt}")
        elif rec["attempts"] >= MAX_ATTEMPTS or want is not None:  # 링크 업로드는 재시도 회차가 없으니 바로 알림
            rec["status"] = "failed"
            shutil.rmtree(config.POSTS_DIR / rec["id"], ignore_errors=True)
            what = "영상을" if rec.get("type") == "reel" else "링크를"
            notify(f"⚠️ [{ch['handle']}] 업로드 실패 — {what} 다시 보내줘\n{title}\n{err}")
        else:
            notify(f"⚠️ [{ch['handle']}] 업로드 실패 (다음 회차에 재시도)\n{title}\n{err}")
        rec["done"] = datetime.now(timezone.utc).isoformat()
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
        if os.environ.get("GITHUB_ACTIONS") == "true":
            git_save(f"published {rec['id']}")

    # 업로드 도중 멈춘 잠금은 30분 뒤 풀어줌 (다음 실행이 다시 시도)
    for p in config.PENDING_DIR.glob("*.json"):
        rec = json.loads(p.read_text())
        if rec.get("status") == "publishing" and \
                datetime.now(timezone.utc) - datetime.fromisoformat(rec.get("claimed", rec["created"])) > timedelta(minutes=30):
            rec["status"] = "pending"
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    # 처리 끝난 기록은 2일 뒤 정리
    now = datetime.now(timezone.utc)
    for p in config.PENDING_DIR.glob("*.json"):
        rec = json.loads(p.read_text())
        if rec.get("status") not in ("pending", "publishing", "awaiting") and \
                (now - datetime.fromisoformat(rec.get("done", rec["created"]))).days >= 2:
            p.unlink()


if __name__ == "__main__":
    main()
