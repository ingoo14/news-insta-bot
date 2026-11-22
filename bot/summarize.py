"""Gemini(무료 티어)로 기사 1건 선별 + 카드 문구·캡션 요약 작성."""
import json
import re
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
            r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=(10, 75))
        except requests.exceptions.RequestException as e:  # 시간 초과 등
            last = str(e)
            print(f"[{model} 응답 지연 {i + 1}] {e}", flush=True)
            i += 1
            time.sleep(3)
            continue
        if r.status_code == 400 and "thinking" in r.text.lower() and "thinkingConfig" in gen:
            gen = {k: v for k, v in gen.items() if k != "thinkingConfig"}  # 옵션 미지원 모델이면 빼고 재시도
            continue
        if r.status_code in (404, 403):  # 모델 없음/권한 없음 → 바로 다음 모델
            return None, f"{r.status_code} {r.text[:200]}"
        if r.status_code == 429 or r.status_code >= 500:  # 한도/혼잡 → 잠시 대기
            last = f"{r.status_code} {r.text[:200]}"
            print(f"[{model} 대기 {i + 1}] {r.status_code}", flush=True)
            i += 1
            time.sleep(8 * i)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini 오류 {r.status_code}: {r.text[:500]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return _parse_json(text), None
    return None, last


CLAUDE_API = "https://api.anthropic.com/v1/messages"


def _parse_json(text: str):
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except Exception:
        import re
        m = re.search(r"[\[{].*[\]}]", text, re.S)  # 앞뒤에 말이 붙어 있어도 JSON 부분만
        if m:
            return json.loads(m.group(0))
        raise


def _claude(prompt: str, tries: int = 3, image: str | None = None):
    """Claude Haiku (유료, 기본). 실패하면 None → Gemini로 넘어감."""
    key = config.env("ANTHROPIC_API_KEY")
    if not key:
        return None
    model = config.env("CLAUDE_MODEL", "claude-haiku-5-5")
    body = {"model": model, "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt + "\n\n설명 없이 JSON만 출력해."}]}
    if image:  # 캡처 이미지 같이 보내기
        import base64
        b64 = base64.b64encode(open(image, "rb").read()).decode()
        body["messages"][0]["content"] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": prompt + "\n\n설명 없이 JSON만 출력해."}]
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    t0 = time.time()
    for i in range(tries):
        try:
            r = requests.post(CLAUDE_API, headers=headers, json=body, timeout=(10, 90))
        except requests.exceptions.RequestException as e:
            print(f"[Claude 응답 지연 {i + 1}] {e}", flush=True)
            time.sleep(3)
            continue
        if r.status_code in (429, 500, 502, 503, 529):  # 혼잡 → 잠깐 쉬고 재시도
            print(f"[Claude 대기 {i + 1}] {r.status_code}", flush=True)
            time.sleep(5 * (i + 1))
            continue
        if r.status_code >= 400:
            print(f"[Claude 오류 → Gemini로 전환] {r.status_code} {r.text[:200]}", flush=True)
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        try:
            data = _parse_json(text)
            print(f"[Claude] {model} {time.time() - t0:.0f}초", flush=True)
            return data
        except Exception as e:
            print(f"[Claude 응답 형식 오류 {i + 1}] {e}", flush=True)
    return None


def _gemini(prompt: str) -> dict:
    """1순위 Claude Haiku → 막히면 Gemini (여러 무료 모델 자동 전환)."""
    data = _claude(prompt)
    if data is not None:
        return data
    return _gemini_only(prompt)


def _gemini_only(prompt: str) -> dict:
    """Gemini 기본 모델 → 혼잡하면 다른 무료 모델로 자동 전환."""
    t0 = time.time()
    data, err = _call(config.GEMINI_MODEL, prompt, tries=2)  # 느리면 빨리 다른 모델로
    if data is not None:
        print(f"[Gemini] {config.GEMINI_MODEL} {time.time() - t0:.0f}초", flush=True)
        return data
    for m in _fallback_models():
        print(f"[모델 전환] {config.GEMINI_MODEL} 혼잡 → {m}")
        data, err = _call(m, prompt, tries=1)
        if data is not None:
            print(f"[Gemini] {m} 사용 (총 {time.time() - t0:.0f}초)", flush=True)
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
- 서로 다른 인물로 고를 것. 같은 소식이면 여러 언론사 중 하나만, 가능하면 언론사도 골고루. 실시간 이벤트(예: 시상식)는 같은 행사라도 인물·수상·무대가 다르면 각각 골라도 됨.
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


def write_story(a: dict, manual: bool = False) -> dict:
    extra = ("\n[직접 요청 기사] 운영자가 직접 골라 보낸 기사야. 주제 때문에 거절하지 말고 ok는 true로, "
             "기사에 적힌 사실만 담담하게 정리해 (추측·자극적 표현은 여전히 금지).\n") if manual else ""
    prompt = f"""너는 한국 연예뉴스 인스타그램 계정의 에디터야. 아래 기사 1건으로 게시물을 만든다.
{RULES}
[카드 이미지 문구] — 기사 제목처럼 짧고 명확하게, 사진 아래에 큰 글씨로 들어감
- headline_top: 흰 글씨. 작은따옴표는 ‘ ’ 사용. '누가, 무엇' (예: "탑, '다중관점'", "김향기·차학연 '로맨스의 절댓값'"). 14자 이내.
- headline_bottom: 컬러 강조 글씨. 핵심 소식 (예: "트랙리스트 공개", "새 연프 패널 합류", "4월 17일 공개"). 12자 이내.
- tag: 분야 한 단어 (연예/음악/드라마/영화/예능 중)
- subline: 한 줄 부제 (캡션용). 22자 이내.
[2장째 요약 카드]
- card_summary: 기사 핵심 사실 3~4문장. 문장당 32자 이내, '~했다/~예정' 체. 누가·언제·어디서·무엇을 위주, 해석·추측 금지.
[인스타 캡션]
- title: 캡션 첫 줄 제목. '"짧은 인용"... 누가 무엇' 형식 추천. 40자 이내.
- body: 본문 3~5문단. 문단당 1~2문장, '~했습니다' 체. 기사에 적힌 사실만 자기 말로 재구성.
  ⚠️ 해석·추측·의미 부여·과장 금지 (예: "~를 앞당겨 이룬 셈", "~할 것으로 보인다", "기대가 모인다" 같은 문장 쓰지 말 것).
  ⚠️ 이름·날짜·장소·숫자·작품명은 기사 표기 그대로. 기사에 없는 정보는 절대 추가하지 말 것. 이모지는 1개까지.
- hashtags: 8~12개, '#' 포함. 인물명(한글/영문), 프로그램명 등.
[사진 검색어]
- photo_queries: 이 기사의 핵심 인물(또는 그룹) 이름 2~4개. 기사 사진으로 쓸 사람 순서대로.
  한글 공식 이름 먼저, 그다음 영문 공식 표기 (예: ["에픽하이", "Epik High", "타블로", "Tablo"]). 인물이 없으면 [].
- person: 표지에 나올 핵심 인물 한글 이름 1개 (예: "에픽하이").
- scene_queries: 인물 사진이 없을 때 쓸 분위기 사진 영어 검색어 2개. 기사 장면과 어울리게 (예: "Han River park Seoul", "concert stage lights").
기사 내용이 부실하거나 원칙 위반 소지가 있으면 ok를 false로 하고 reason에 이유를 한 줄로.
{extra}
언론사: {a['press']}
제목: {a['title']}
본문:
{a['body']}

JSON으로만 답해:
{{"ok": true, "reason": "", "tag": "...", "headline_top": "...", "headline_bottom": "...", "subline": "...", "card_summary": ["...", "...", "..."], "title": "...", "body": "...", "hashtags": ["#..."], "photo_queries": ["..."], "person": "...", "scene_queries": ["..."]}}"""
    s = normalize(_gemini(prompt))
    if manual and s.get("body") and s.get("headline_top"):
        s["ok"] = True
    if s.get("ok") and s.get("body"):
        s = fact_check(s, a)
    s["press"], s["url"], s["source_title"] = a["press"], a["url"], a["title"]
    return s


def fact_check(s: dict, a: dict) -> dict:
    """작성된 문구를 기사 원문과 대조해서 기사에 없는 내용·해석·오류를 고침 (같은 키로 돌려받음)."""
    keys = ["headline_top", "headline_bottom", "card_summary", "title", "body"]
    draft = {k: s.get(k) for k in keys}
    prompt = f"""너는 깐깐한 뉴스 교열기자야. 아래 [초안]을 [기사 원문]과 한 문장씩 대조해서 고쳐.
- 기사에 없는 내용, 해석·추측·의미 부여·과장("~한 셈", "~로 보인다", "기대가 모인다" 등)은 삭제.
- 이름·날짜·장소·숫자·작품명·행사명이 기사와 다르면 기사대로 수정.
- 어색한 한국어는 자연스럽게 다듬되, 기사 문장을 그대로 베끼지는 말 것.
- 길이 규칙 유지: headline_top 14자, headline_bottom 12자 이내, card_summary 문장당 32자 이내.
- 내용이 맞으면 그대로 둬.

[기사 원문]
제목: {a['title']}
{a['body']}

[초안]
{json.dumps(draft, ensure_ascii=False, indent=1)}

같은 키의 JSON으로만 답해."""
    try:
        fixed = normalize(_gemini(prompt))
        for k in keys:
            if fixed.get(k):
                s[k] = fixed[k]
        s["fact_checked"] = True
    except Exception as e:
        print(f"[팩트체크 건너뜀] {e}")
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


def read_capture(image: str) -> dict | None:
    """인스타 등 캡처 화면에서 계정·문구·상황을 읽음 (얼굴로 사람을 판별하지 않음)."""
    prompt = """이건 연예인/아티스트 SNS(주로 인스타그램) 게시물 캡처야. 화면에 '글자로 보이는 것'과 사진 속 '사물·상황'만 읽어.
⚠️ 얼굴 생김새로 누구인지 추측하지 마. 누구인지는 계정 이름, 게시글 문구, 해시태그, 음악 스티커 등 글자로만 판단해.
JSON:
{"account": "게시한 계정 아이디 (예: rescene_official)",
 "person": "계정·글자로 확인되는 아티스트 한글 이름 (예: rescene_official → 리센느). 계정 아이디가 널리 알려진 연예인 개인 계정이면 그 이름 (예: for_everyoung10 → 장원영). 확신 없으면 빈 문자열",
 "person_en": "영문 이름 (예: RESCENE)",
 "caption": "게시글 문구 그대로 (해시태그 포함)",
 "date": "글자로 보이는 날짜 (예: 261120 → 2026-11-20). 없으면 빈 문자열",
 "music": "음악 스티커 곡 정보 (있으면)",
 "scene": "사진 속 상황을 사실대로 한 문장 (예: 흰 드레스를 입은 다섯 명이 트로피를 들고 포즈). 사물·옷·장소만, 사람 신원 추측 금지",
 "search_query": "이 게시물 관련 뉴스를 찾을 검색어 2~4단어 (예: 리센느 수상)"}"""
    data = _claude(prompt, image=image)
    return data if isinstance(data, dict) else None


def write_capture_story(info: dict, news_items: list[dict]) -> dict:
    """캡처 내용 + 실제 뉴스 헤드라인으로 긍정적인 소식 글 작성."""
    heads = "\n".join(f"- [{n.get('press', '')}] {n['title']} :: {n.get('snippet', '')[:150]}" for n in news_items) or "(관련 뉴스 없음)"
    prompt = f"""너는 한국 연예뉴스 인스타그램 계정의 에디터야. 아티스트 SNS 게시물 캡처를 소식 게시물로 만든다.
[원칙]
- 밝고 긍정적인 톤 (축하·응원·반가움). 부정적·자극적·비꼬는 표현 금지.
- 사실은 아래 [캡처 정보]와 [관련 뉴스]에 있는 것만. 수상명·행사명·날짜·곡명은 [관련 뉴스]나 캡처 글자로 확인될 때만 쓸 것.
  예: 캡처에 트로피만 보이고 뉴스에서 '리센느 MAMA 수상'이 확인되면 → "리센느, MAMA 수상" OK.
      뉴스로 확인 안 되면 → 행사명 없이 "리센느, 트로피 들고 환한 미소"처럼 보이는 사실만.
- ⭐ [캡처 정보]의 memo_from_sender는 운영자가 직접 확인하고 적어준 내용이야. 사실로 믿고 반드시 반영해.
  메모에 행사명(예: MAMA)·소식이 있으면 headline_top/headline_bottom/title에 꼭 넣고 confirmed=true. 메모가 제목처럼 쓰여 있으면 그 표현을 최대한 살려.
- 관련 뉴스 중 이 게시물과 무관한 건 무시. 추측·해석("~로 보인다", "기대가 모인다") 금지.
- '인스타그램 근황 공개' 같은 뻔한 제목보다, 소식 자체(수상·컴백·공연·시상식 참석 등)를 제목으로.
- [관련 뉴스]에 같은 인물의 최근 행사(시상식·레드카펫·공연 등)가 있고, 보낸 사람 메모나 게시글 문구·날짜·의상 묘사가 그 행사와 맞으면 그 행사 소식으로 써.
- 제목·본문에 반드시 인물 이름을 넣을 것. 사진 묘사(옷·포즈)만 늘어놓지 말고, 무슨 소식인지가 먼저.
- "앞으로도 전해 드리겠습니다", "기대가 모인다" 같은 빈 문장 금지.

[캡처 정보]
{json.dumps(info, ensure_ascii=False, indent=1)}

[관련 뉴스 (최근 며칠, 제목 :: 요약)]
{heads}

[출력]
- headline_top: 흰 글씨 '누가, 무엇' 14자 이내 (예: "리센느, MAMA 트로피")
- headline_bottom: 컬러 강조 핵심 12자 이내 (예: "수상 인증샷 공개"). 첫 줄과 같은 단어 반복 금지 (예: 'MAMA 수상' / '베스트 오브 넥스트 수상' ✗ → 'MAMA 트로피' / '베스트 오브 넥스트' ✓)
- tag: 음악/드라마/영화/예능/연예 중 하나
- subline: 22자 이내 부제
- title: 캡션 첫 줄 제목 40자 이내
- body: 3~4문단, 문단당 1~2문장, '~했습니다' 체, 따뜻한 톤. 이모지 1개까지.
- hashtags: 8~12개 ('#' 포함). 이 게시물의 주인공(본인·소속 그룹)과 행사·작품명만.
  ⚠️ 관련 뉴스에 같이 나온 다른 멤버·다른 연예인 이름은 절대 넣지 말 것 (사진에 없는 사람).
- person: 아티스트 한글 이름
- confirmed: 행사명/수상명을 관련 뉴스로 확인했으면 true

JSON:
{{"tag": "...", "headline_top": "...", "headline_bottom": "...", "subline": "...", "title": "...", "body": "...", "hashtags": ["#..."], "person": "...", "confirmed": false}}"""
    s = normalize(_gemini(prompt))
    if info.get("memo_from_sender"):
        s["confirmed"] = True  # 운영자가 적어준 내용 = 확인된 사실
    # 뉴스 제목에 함께 나온 다른 사람 이름(예: '장원영·안유진')은 해시태그에서 제거
    me = (info.get("person") or s.get("person") or "").replace(" ", "")
    others = set()
    for n in news_items:
        for grp in re.findall(r"[가-힣]{2,4}(?:\s*[·&ㆍ]\s*[가-힣]{2,4})+", n.get("title", "")):
            for name in re.split(r"\s*[·&ㆍ]\s*", grp):
                if name and me and name != me and me not in name:
                    others.add(name)
    if others:
        before = list(s.get("hashtags", []))
        s["hashtags"] = [h for h in before if not any(o in h.lstrip("#") for o in others)]
        dropped = set(before) - set(s["hashtags"])
        if dropped:
            print(f"[해시태그 정리] 다른 사람 이름 제거: {sorted(dropped)}")
    s["ok"] = bool(s.get("headline_top") and s.get("body"))
    return s
