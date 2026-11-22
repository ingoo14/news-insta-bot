"""인스타그램 캐러셀 업로드 (통합 로그인 + 시스템 사용자 토큰 → graph.facebook.com)."""
import json
import time

import requests

from . import config


def _url(path):
    return f"https://{config.IG_GRAPH_HOST}/{config.IG_API_VERSION}/{path}"


def _post(path, **params):
    params["access_token"] = config.IG_ACCESS_TOKEN
    r = requests.post(_url(path), data=params, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Instagram {path} 실패: {data['error'].get('message')}")
    return data


def _get(path, **params):
    params["access_token"] = config.IG_ACCESS_TOKEN
    data = requests.get(_url(path), params=params, timeout=30).json()
    if "error" in data:
        raise RuntimeError(f"Instagram {path} 실패: {data['error'].get('message')}")
    return data


def _wait(container_id, tries=20):
    for _ in range(tries):
        st = _get(container_id, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"컨테이너 {container_id} 상태: {st} (이미지 URL 접근 가능한지 확인)")
        time.sleep(3)
    raise RuntimeError(f"컨테이너 {container_id} 처리 시간 초과")


POLL_NOTE = ""  # 투표를 못 붙였을 때 이유 (알림용)


def _poll_params(poll: list[str] | None) -> dict:
    """투표(2~4개 선택지, 각 25자) → 인스타 caption add-on 파라미터 (2026.9 추가 기능)."""
    opts = [o.strip()[:25] for o in (poll or []) if o and o.strip()][:4]
    if len(opts) < 2:
        return {}
    keys = ["option_a", "option_b", "option_c", "option_d"]
    return {"poll_attachment": json.dumps(dict(zip(keys, opts)), ensure_ascii=False)}


def _create_with_poll(uid: str, params: dict, poll) -> str:
    """투표를 붙여서 컨테이너 생성. 투표 때문에 실패하면 투표 없이 다시 (게시물은 꼭 올라가게)."""
    global POLL_NOTE
    pp = _poll_params(poll)
    if pp:
        try:
            return _post(f"{uid}/media", **params, **pp)["id"]
        except Exception as e:
            POLL_NOTE = f"투표는 못 붙였어 ({str(e)[:120]})"
            print(f"[투표 실패 → 투표 없이 업로드] {e}")
    return _post(f"{uid}/media", **params)["id"]


def publish_carousel(image_urls: list[str], caption: str, poll=None) -> str:
    uid = config.IG_USER_ID
    children = []
    for u in image_urls[:10]:
        c = _post(f"{uid}/media", image_url=u, is_carousel_item="true")["id"]
        _wait(c)
        children.append(c)
    params = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption[:2200]}
    if config.IG_AI_LABEL:
        params["is_ai_generated"] = "true"
    carousel = _create_with_poll(uid, params, poll)
    _wait(carousel)
    media_id = _post(f"{uid}/media_publish", creation_id=carousel)["id"]
    try:
        return _get(media_id, fields="permalink").get("permalink", "")
    except Exception:
        return ""


def publish_single(image_url: str, caption: str, poll=None) -> str:
    uid = config.IG_USER_ID
    params = {"image_url": image_url, "caption": caption[:2200]}
    if config.IG_AI_LABEL:
        params["is_ai_generated"] = "true"
    c = _create_with_poll(uid, params, poll)
    _wait(c)
    media_id = _post(f"{uid}/media_publish", creation_id=c)["id"]
    try:
        return _get(media_id, fields="permalink").get("permalink", "")
    except Exception:
        return ""


def publish(image_urls: list[str], caption: str, poll=None) -> str:
    global POLL_NOTE
    POLL_NOTE = ""
    if len(image_urls) == 1:
        return publish_single(image_urls[0], caption, poll)
    return publish_carousel(image_urls, caption, poll)


def refresh_token() -> str:
    """장기 토큰(60일) 연장. 새 토큰 문자열을 돌려줌."""
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": config.IG_ACCESS_TOKEN}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"토큰 갱신 실패: {data}")
    return data["access_token"]
