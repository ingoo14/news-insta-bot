"""Gemini(무료 티어)로 기사 1건 선별 + 카드 문구·캡션 요약 작성."""
import json
import time

import requests

from . import config

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RULES = """
[원칙]
- 사실로 확인된 소식만. 루머, 열애·결별 추측, 사생활 폭로, 확정되지 않은 범죄·논란 의혹, 사망·사고 자극 보도는 제외.
- 특정 인물·소속사를 비방하거나 단정하는 표현 금지. 기사에 없는 내용 지어내지 말 것.
- 기사 문장을 그대로 베끼지 말고 반드시 자기 말로 새로 쓸 것. 직접 인용은 짧은 것 2개까지만.
- 컴백·신곡·콘서트·드라마/영화·예능·수상·차트·재미있는 에피소드처럼 화제성 있고 긍정적인 소식 우선.
"""


def _gemini(prompt: str, retries: int = 4) -> dict:
    url = API.format(model=config.GEMINI_MODEL)
    gen = {"responseMimeType": "application/json", "temperature": 0.5,
           "thinkingConfig": {"thinkingLevel": "low"}}  # 깊은 생각 끄기 → 빠르게
    last = None
    for i in range(retries):
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
        try:
            r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=240)
        except requests.exceptions.RequestException as e:  # 시간 초과 등 → 잠시 후 재시도
            last = str(e)
            print(f"[Gemini 재시도 {i + 1}] {e}")
            time.sleep(10)
            continue
        if r.status_code == 400 and "thinking" in r.text.lower() and "thinkingConfig" in gen:
            gen = {k: v for k, v in gen.items() if k != "thinkingConfig"}  # 모델이 옵션 미지원이면 빼고 재시도
            continue
        if r.status_code == 429 or r.status_code >= 500:  # 무료 티어 한도/서버 혼잡 → 잠시 대기
            last = r.text[:300]
            print(f"[Gemini 대기 {i + 1}] {r.status_code}")
            time.sleep(20 * (i + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:500]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(text)
    raise RuntimeError(f"Gemini 호출 실패: {last}")


def pick(candidates: list[dict], n: int, recent_topics: list[str]) -> list[int]:
    lines = [f"{i}. [{c['press']}] {c['title']} — {c['snippet'][:80]}" for i, c in enumerate(candidates[:50])]
    recent = "\n".join(f"- {t}" for t in recent_topics[-30:]) or "(없음)"
    prompt = f"""너는 한국 연예뉴스 인스타그램 계정의 편집자야.
아래 최근 기사 목록에서 인스타 게시물(기사 1건 = 게시물 1개)로 만들 기사를 우선순위대로 {n}개 골라.
앞 순위일수록 화제성 있고 사람들이 반응할 만한 소식. 서로 다른 인물·사건으로 고를 것.
유명 가수·배우·그룹 소식을 우선 (사진을 찾기 쉬움).
최근에 이미 올린 소식과 같은 사건·같은 인물의 같은 이슈는 제외.
{RULES}
[최근에 이미 올린 소식]
{recent}

[기사 목록]
{chr(10).join(lines)}

JSON으로만 답해: {{"picks": [번호, ...]}}"""
    picks = _gemini(prompt).get("picks", [])
    return [int(p) for p in picks if 0 <= int(p) < min(len(candidates), 50)][:n]


def write_story(a: dict) -> dict:
    prompt = f"""너는 한국 연예뉴스 인스타그램 계정의 에디터야. 아래 기사 1건으로 게시물을 만든다.
{RULES}
[카드 이미지 문구]
- tag: 분야 한 단어 (연예/음악/드라마/영화/예능 중)
- headline_top: 카드 1줄째 흰 글씨. 인물의 재치 있는 한마디(짧은 따옴표)나 핵심 상황. 16자 이내.
- headline_bottom: 카드 2줄째 강조(노란) 글씨. '누가 무엇을' 핵심. 14자 이내.
- subline: 카드 하단 부제. 22자 이내.
[인스타 캡션]
- title: 캡션 첫 줄 제목. '"짧은 인용"... 누가 무엇' 형식 추천. 40자 이내.
- body: 본문 4~6문단. 문단당 1~3문장, '~했습니다' 체. 기사 사실만 자기 말로 재구성. 마지막 문단은 앞으로의 일정/의미. 이모지는 전체에 1~2개만.
- hashtags: 8~12개, '#' 포함. 인물명(한글/영문), 프로그램명 등.
[사진 검색어]
- photo_queries: 이 기사의 핵심 인물 사진을 찾을 검색어 2~3개. 영문 공식 표기 우선 (예: "Epik High", "Tablo"), 마지막에 한글 이름. 인물이 없으면 [].
- scene_queries: 인물 사진이 없을 때 쓸 분위기 사진 영어 검색어 2개. 기사 장면과 어울리게 (예: "Han River park Seoul", "concert stage lights").
기사 내용이 부실하거나 원칙 위반 소지가 있으면 ok를 false로.

언론사: {a['press']}
제목: {a['title']}
본문:
{a['body']}

JSON으로만 답해:
{{"ok": true, "tag": "...", "headline_top": "...", "headline_bottom": "...", "subline": "...", "title": "...", "body": "...", "hashtags": ["#..."], "photo_queries": ["..."], "scene_queries": ["..."]}}"""
    s = _gemini(prompt)
    s["press"], s["url"], s["source_title"] = a["press"], a["url"], a["title"]
    return s
