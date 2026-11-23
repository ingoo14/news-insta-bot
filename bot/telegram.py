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
    try:
        info = _api("getFile", json={"file_id": file_id})
    except Exception as e:
        if "too big" in str(e).lower():
            raise RuntimeError("파일이 20MB를 넘어서 봇이 못 받아 (텔레그램 봇 제한). '파일'이 아니라 일반 동영상으로 보내서 압축되게 하거나, 화질을 낮춰서 다시 보내줘")
        raise
    url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{info['file_path']}"
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest


def download_big(chat_id: str, message_id: int, dest: Path) -> Path:
    """20MB 넘는 영상: 봇 API 대신 텔레그램 본 서버(MTProto)로 받기 (봇도 2GB까지 가능).
    필요: GitHub Secrets TG_API_ID, TG_API_HASH (my.telegram.org에서 무료 발급)."""
    import asyncio
    import os
    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not (api_id and api_hash):
        raise RuntimeError("20MB 넘는 영상은 TG_API_ID / TG_API_HASH 설정이 필요해 (안내 참고)")
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import PeerChannel, PeerChat

    async def run():
        async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
            await client.start(bot_token=config.TELEGRAM_BOT_TOKEN)
            cid = int(chat_id)
            if str(chat_id).startswith("-100"):
                peer = PeerChannel(int(str(chat_id)[4:]))
            elif cid < 0:
                peer = PeerChat(-cid)
            else:
                peer = cid
            msg = await client.get_messages(peer, ids=int(message_id))
            if not msg or not msg.media:
                raise RuntimeError("텔레그램에서 그 메시지의 영상을 못 찾았어")
            dest.parent.mkdir(parents=True, exist_ok=True)
            await client.download_media(msg, file=str(dest))
    asyncio.run(run())
    print(f"[큰 파일 받기] {dest.stat().st_size / 1e6:.1f}MB")
    return dest
