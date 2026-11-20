"""Gemini(무료 티어)로 기사 선별 + 요약 + 캡션 생성."""
import json
import time

import requests

from . import config

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RULES = """
[원칙]
- 사실로 확인된 소식만. 루머, 열애·결별 추측, 사생활 폭로, 확정되지 않은 범죄·논란 의혹, 사망·사고 자극 보도는 제외.
- 특정 인물·소속사를 비방하거나 단정하는 표현 금지. 기사에 없는 내용 지어내지 말 것.
- 기사 문장을 그대로 베끼지 말고 반드시 자기 말로 새로 쓸 것.
- 컴백·신곡·드라마/영화 캐스팅·방송·공연·수상·차트 같은 긍정적이고 화제성 있는 소식 우선.
"""


def _gemini(prompt: str, retries: int = 3) -> dict:
    url = API.format(model=config.GEMINI_MODEL)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    last = None
    for i in range(retries):
        r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=90)
        if r.status_code == 429 or r.status_code >= 500:  # 무료 티어 분당 한도 → 잠시 대기
            last = r.text
            time.sleep(20 * (i + 1))
            continue
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    raise RuntimeError(f"Gemini 호출 실패: {last}")


def pick(candidates: list[dict], n: int) -> list[int]:
    lines = [f"{i}. [{c['press']}] {c['title']} — {c['snippet'][:120]}" for i, c in enumerate(candidates[:60])]
    prompt = f"""너는 한국 연예뉴스 인스타그램 계정의 편집자야.
아래 최근 기사 목록에서 카드뉴스로 올릴 기사 {n}개를 골라.
서로 다른 인물/주제로 고르고, 같은 사건을 다룬 기사는 하나만.
{RULES}
[기사 목록]
{chr(10).join(lines)}

JSON으로만 답해: {{"picks": [번호, ...]}}"""
    picks = _gemini(prompt).get("picks", [])
    return [int(p) for p in picks if 0 <= int(p) < len(candidates)][:n]


def write_post(articles: list[dict]) -> dict:
    blocks = []
    for i, a in enumerate(articles):
        blocks.append(f"### 기사 {i}\n언론사: {a['press']}\n제목: {a['title']}\n본문:\n{a['body']}")
    prompt = f"""너는 한국 연예뉴스 인스타그램 계정의 에디터야. 아래 기사들로 카드뉴스를 만든다.
{RULES}
[작성 규칙]
- headline: 카드 제목. 22자 이내, 핵심 인물 이름 포함, 낚시성 금지.
- summary: 3줄 요약. 각 줄 38자 이내, 존댓말 아닌 간결한 '~했다/~예정' 체.
- cover_title: 표지 문구. 16자 이내 (예: "오늘 놓치면 안 될 연예 소식").
- caption: 인스타 본문. 첫 줄은 훅 한 문장, 이후 각 소식을 한 줄씩(이모지 1개씩), 마지막에 '출처: 언론사명들' 표기. 600자 이내.
- hashtags: 관련 해시태그 12개 이내, '#' 포함.
- 기사 내용이 부실하거나 원칙 위반 소지가 있으면 그 기사는 items에서 빼.

{chr(10).join(blocks)}

JSON으로만 답해:
{{"cover_title": "...", "items": [{{"idx": 기사번호, "headline": "...", "summary": ["...", "...", "..."]}}], "caption": "...", "hashtags": ["#..."]}}"""
    data = _gemini(prompt)
    items = []
    for it in data.get("items", []):
        idx = int(it.get("idx", -1))
        if 0 <= idx < len(articles) and it.get("headline") and it.get("summary"):
            a = articles[idx]
            items.append({
                "headline": it["headline"].strip(),
                "summary": [s.strip() for s in it["summary"]][:3],
                "press": a["press"], "url": a["url"], "title": a["title"],
            })
    data["items"] = items
    return data
