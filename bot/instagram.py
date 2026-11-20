"""인스타그램 캐러셀 업로드 (Instagram API with Instagram Login, graph.instagram.com)."""
import time

import requests

from . import config


def _url(path):
    return f"https://graph.instagram.com/{config.IG_API_VERSION}/{path}"


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


def publish_carousel(image_urls: list[str], caption: str) -> str:
    uid = config.IG_USER_ID
    children = []
    for u in image_urls[:10]:
        c = _post(f"{uid}/media", image_url=u, is_carousel_item="true")["id"]
        _wait(c)
        children.append(c)
    params = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption[:2200]}
    if config.IG_AI_LABEL:
        params["is_ai_generated"] = "true"
    carousel = _post(f"{uid}/media", **params)["id"]
    _wait(carousel)
    media_id = _post(f"{uid}/media_publish", creation_id=carousel)["id"]
    try:
        return _get(media_id, fields="permalink").get("permalink", "")
    except Exception:
        return ""


def refresh_token() -> str:
    """장기 토큰(60일) 연장. 새 토큰 문자열을 돌려줌."""
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": config.IG_ACCESS_TOKEN}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"토큰 갱신 실패: {data}")
    return data["access_token"]
