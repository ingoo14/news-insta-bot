"""텔레그램 봇: 미리보기 전송, 승인 버튼, 응답 수신."""
import json
from pathlib import Path

import requests

from . import config


def _api(method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}", timeout=60, **kw)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} 실패: {data}")
    return data["result"]


def send_message(text, buttons=None, reply_to=None):
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return _api("sendMessage", json=payload)


def send_album(paths: list[Path]):
    media, files = [], {}
    for i, p in enumerate(paths[:10]):
        key = f"f{i}"
        media.append({"type": "photo", "media": f"attach://{key}"})
        files[key] = (p.name, open(p, "rb"), "image/jpeg")
    try:
        return _api("sendMediaGroup", data={"chat_id": config.TELEGRAM_CHAT_ID, "media": json.dumps(media)}, files=files)
    finally:
        for f in files.values():
            f[1].close()


def send_photo(path: Path, caption: str = ""):
    with open(path, "rb") as f:
        return _api("sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                    files={"photo": (path.name, f, "image/jpeg")})


def get_updates(offset: int):
    return _api("getUpdates", json={"offset": offset, "timeout": 0, "allowed_updates": ["callback_query", "message"]})


def answer_callback(cb_id, text):
    try:
        _api("answerCallbackQuery", json={"callback_query_id": cb_id, "text": text})
    except Exception:
        pass  # 버튼 누른 지 오래되면 실패하는데 무시해도 됨


def edit_buttons(message_id, label):
    try:
        _api("editMessageReplyMarkup", json={
            "chat_id": config.TELEGRAM_CHAT_ID, "message_id": message_id,
            "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]},
        })
    except Exception:
        pass


def download_file(file_id: str, dest: Path) -> Path:
    """텔레그램으로 받은 사진을 파일로 저장."""
    info = _api("getFile", json={"file_id": file_id})
    url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{info['file_path']}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest
