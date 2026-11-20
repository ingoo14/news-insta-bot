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


_fallbacks = None
SKIP = ("embedding", "tts", "image", "live", "audio", "veo", "imagen", "aqa", "learnlm", "gemma", "robotics", "computer")


def _fallback_models() -> list[str]:
    """혼잡할 때 갈아탈 다른 텍스트 모델 목록 (API에서 사용 가능한 모델을 조회)."""
    global _fallbacks
    if _fallbacks is not None:
        return _fallbacks
    names = [m.strip() for m in (config.env("GEMINI_FALLBACK_MODELS", "") or "").split(",") if m.strip()]
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": config.GEMINI_API_KEY, "pageSize": 200}, timeout=30)
        for m in r.json().get("models", []):
            n = m.get("name", "").removeprefix("models/")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if any(k in n.lower() for k in SKIP) or n in names:
                continue
            names.append(n)
    except Exception as e:
        print(f"[모델 목록 조회 실패] {e}")
    # 가벼운 모델(flash/lite) 우선, 최신 이름 우선
    names.sort(key=lambda n: (0 if ("flash" in n or "lite" in n or "hydrogen" in n) else 1,
                              "preview" in n or "exp" in n, [-ord(c) for c in n]))
    _fallbacks = [n for n in names if n != config.GEMINI_MODEL][:5]
    print(f"[대체 모델 후보] {_fallbacks}")
    return _fallbacks


def _call(model: str, prompt: str, tries: int):
    url = API.format(model=model)
    gen = {"responseMimeType": "application/json", "temperature": 0.5,
           "thinkingConfig": {"thinkingLevel": "low"}}  # 깊은 생각 끄기 → 빠르게
    last = None
    i = 0
    while i < tries:
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
        try:
            r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=240)
        except requests.exceptions.RequestException as e:  # 시간 초과 등
            last = str(e)
            print(f"[{model} 재시도 {i + 1}] {e}")
            i += 1
            time.sleep(10)
            continue
        if r.status_code == 400 and "thinking" in r.text.lower() and "thinkingConfig" in gen:
            gen = {k: v for k, v in gen.items() if k != "thinkingConfig"}  # 옵션 미지원 모델이면 빼고 재시도
            continue
        if r.status_code in (404, 403):  # 모델 없음/권한 없음 → 바로 다음 모델
            return None, f"{r.status_code} {r.text[:200]}"
        if r.status_code == 429 or r.status_code >= 500:  # 한도/혼잡 → 잠시 대기
            last = f"{r.status_code} {r.text[:200]}"
            print(f"[{model} 대기 {i + 1}] {r.status_code}")
            i += 1
            time.sleep(15 * i)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:500]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(text), None
    return None, last


def _gemini(prompt: str) -> dict:
    """기본 모델 → 혼잡하면 다른 무료 모델로 자동 전환."""
    data, err = _call(config.GEMINI_MODEL, prompt, tries=3)
    if data is not None:
        return data
    for m in _fallback_models():
        print(f"[모델 전환] {config.GEMINI_MODEL} 혼잡 → {m}")
        data, err = _call(m, prompt, tries=2)
        if data is not None:
            return data
    raise RuntimeError(f"Gemini 호출 실패 (모든 모델): {err}")


def pick(candidates: list[dict], n: int, recent_topics: list[str], trend_text: str = "", live_text: str = "") -> list[int]:
    lines = []
    for i, c in enumerate(candidates[:50]):
        hot = f" 🔥{', '.join(c.get('trend_hits', []))}" if c.get("trend_hits") else ""
        live = f" ⚡실시간:{','.join(c['live_hits'])}" if c.get("live_hits") else ""
        age = c.get("age_min")
        age_s = f"{age}분 전" if age is not None and age < 120 else f"{age // 60}시간 전" if age is not None else ""
        lines.append(f"{i}. [{age_s}][{c['press']}] {c['title']} — {c['snippet'][:60]}{hot}{live}")
    recent = "\n".join(f"- {t}" for t in recent_topics[-30:]) or "(없음)"
    prompt = f"""너는 트래픽을 최우선으로 하는 한국 연예뉴스 인스타그램 계정의 편집장이야.
아래 기사 목록에서 게시물(기사 1건 = 게시물 1개)로 만들 기사를 우선순위대로 {n}개 골라.

[선정 기준 — 중요도 순]
1. 실시간성: 지금 벌어지는 일 (⚡ = 지금 기사가 몰리는 실시간 이벤트, 예: 시상식·콘서트·쇼케이스 진행 중). 최근 1~2시간 기사 우선.
2. 분야: 음악(가수·아이돌·시상식·차트·컴백) 최우선 → 드라마 → 영화 → 예능 순.
3. 트래픽: 구글 검색/유튜브 급상승 인물·작품 (🔥 = 트렌드와 일치), 인지도 높은 스타.
4. 사람들이 댓글·공유할 만한 화제성 (수상, 무대, 컴백, 캐스팅, 흥행, 재미있는 에피소드).
- 서로 다른 인물로 고를 것. 실시간 이벤트(예: 시상식)는 같은 행사라도 인물·수상·무대가 다르면 각각 골라도 됨.
- 최근에 이미 올린 소식과 같은 인물의 같은 이슈는 제외.
- 정치·사회·스포츠 기사는 연예인이 중심이 아니면 제외.
{RULES}
[지금 기사가 몰리는 실시간 이벤트 키워드 (최근 2시간)]
{live_text or "(없음)"}

[지금 뜨는 검색/유튜브 키워드]
{trend_text or "(없음)"}

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
[카드 이미지 문구] — 기사 제목처럼 짧고 명확하게, 사진 아래에 큰 글씨로 들어감
- headline_top: 흰 글씨. 작은따옴표는 ‘ ’ 사용. '누가, 무엇' (예: "탑, '다중관점'", "김향기·차학연 '로맨스의 절댓값'"). 14자 이내.
- headline_bottom: 컬러 강조 글씨. 핵심 소식 (예: "트랙리스트 공개", "새 연프 패널 합류", "4월 17일 공개"). 12자 이내.
- tag: 분야 한 단어 (연예/음악/드라마/영화/예능 중)
- subline: 한 줄 부제 (캡션용). 22자 이내.
[2장째 요약 카드]
- card_summary: 기사 핵심을 3~4문장으로. 문장당 32자 이내, '~했다/~예정' 체, 숫자·날짜·작품명 등 구체적인 사실 위주. 자기 말로.
[인스타 캡션]
- title: 캡션 첫 줄 제목. '"짧은 인용"... 누가 무엇' 형식 추천. 40자 이내.
- body: 본문 4~6문단. 문단당 1~3문장, '~했습니다' 체. 기사 사실만 자기 말로 재구성. 마지막 문단은 앞으로의 일정/의미. 이모지는 전체에 1~2개만.
- hashtags: 8~12개, '#' 포함. 인물명(한글/영문), 프로그램명 등.
[사진 검색어]
- photo_queries: 이 기사의 핵심 인물(또는 그룹) 이름 2~4개. 기사 사진으로 쓸 사람 순서대로.
  한글 공식 이름 먼저, 그다음 영문 공식 표기 (예: ["에픽하이", "Epik High", "타블로", "Tablo"]). 인물이 없으면 [].
- person: 표지에 나올 핵심 인물 한글 이름 1개 (예: "에픽하이").
- scene_queries: 인물 사진이 없을 때 쓸 분위기 사진 영어 검색어 2개. 기사 장면과 어울리게 (예: "Han River park Seoul", "concert stage lights").
기사 내용이 부실하거나 원칙 위반 소지가 있으면 ok를 false로.

언론사: {a['press']}
제목: {a['title']}
본문:
{a['body']}

JSON으로만 답해:
{{"ok": true, "tag": "...", "headline_top": "...", "headline_bottom": "...", "subline": "...", "card_summary": ["...", "...", "..."], "title": "...", "body": "...", "hashtags": ["#..."], "photo_queries": ["..."], "person": "...", "scene_queries": ["..."]}}"""
    s = normalize(_gemini(prompt))
    s["press"], s["url"], s["source_title"] = a["press"], a["url"], a["title"]
    return s


def normalize(s) -> dict:
    """모델마다 형식이 달라도(문자열↔목록) 항상 같은 형태로 맞춤."""
    if isinstance(s, list):  # [{...}] 로 감싸서 오는 경우
        s = next((x for x in s if isinstance(x, dict)), {})
    if not isinstance(s, dict):
        return {}
    def to_text(v, sep="\n\n"):
        if isinstance(v, list):
            return sep.join(to_text(x, " ") for x in v if x)
        if isinstance(v, dict):
            return " ".join(to_text(x, " ") for x in v.values() if x)
        return "" if v is None else str(v).strip()
    def to_list(v):
        if isinstance(v, str):
            parts = [x.strip(" -•\t") for x in v.replace("\r", "").split("\n")]
            return [x for x in parts if x]
        if isinstance(v, list):
            return [to_text(x, " ") for x in v if to_text(x, " ")]
        return []
    for k in ("tag", "headline_top", "headline_bottom", "subline", "title", "person"):
        s[k] = to_text(s.get(k), " ")
    s["body"] = to_text(s.get("body"))
    for k in ("card_summary", "photo_queries", "scene_queries"):
        s[k] = to_list(s.get(k))
    tags = s.get("hashtags")
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    s["hashtags"] = [t if t.startswith("#") else "#" + t for t in (to_list(tags) if isinstance(tags, list) else []) if t]
    s["ok"] = s.get("ok") not in (False, "false", "False", 0)
    return s
