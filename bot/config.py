"""설정값 — 전부 GitHub Secrets / Variables 에서 환경변수로 들어옴."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
PENDING_DIR = STATE_DIR / "pending"
POSTS_DIR = ROOT / "docs" / "posts"
SEEN_FILE = STATE_DIR / "seen.json"
OFFSET_FILE = STATE_DIR / "tg_offset.txt"
TOKEN_CHECK_FILE = STATE_DIR / "ig_token_checked.txt"
TOPICS_FILE = STATE_DIR / "recent_topics.json"  # 최근 올린 소식 (같은 사건 중복 방지)


def env(name, default=None, required=False):
    v = os.environ.get(name) or default  # GitHub Variables가 비어 있으면 "" 로 들어와서 기본값 처리
    if required and not v:
        raise SystemExit(f"[설정 누락] 환경변수 {name} 가 비어 있음 (GitHub Secrets 확인)")
    return v


# 뉴스 수집 (언론사 연예 RSS — bot/news.py)
NEWS_LOOKBACK_HOURS = int(env("NEWS_LOOKBACK_HOURS", "8"))
POSTS_PER_RUN = int(env("POSTS_PER_RUN", "2"))  # 회차당 게시물 수 (2시간마다 2건)

# 요약 (Gemini 무료 티어)
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-flash-latest")

# 텔레그램 (업로드 완료 알림용 — OpenClaw와 별개의 새 봇)
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

# 인스타그램 (통합 로그인 + 시스템 사용자 토큰: 만료 없음)
IG_USER_ID = env("IG_USER_ID")
IG_ACCESS_TOKEN = env("IG_ACCESS_TOKEN")
IG_API_VERSION = env("IG_API_VERSION", "v26.0")
IG_GRAPH_HOST = env("IG_GRAPH_HOST", "graph.facebook.com")  # 예전 인스타 로그인 방식이면 graph.instagram.com
IG_TOKEN_REFRESH = env("IG_TOKEN_REFRESH", "false").lower() == "true"  # 시스템 사용자 토큰은 갱신 불필요
IG_AI_LABEL = env("IG_AI_LABEL", "false").lower() == "true"

# 카드 디자인
BRAND_HANDLE = env("BRAND_HANDLE", "@your_account")
BRAND_NAME = env("BRAND_NAME", "연예 브리핑")
TITLE_FONT = (env("TITLE_FONT", "serif") or "serif").lower()  # serif(명조) / sans(고딕)
# 매거진 표지 상단 큰 로고 (영문 추천). 비우면 계정명에서 자동 생성 (@ibduck_music → IBDUCK)
MASTHEAD = env("MASTHEAD") or BRAND_HANDLE.lstrip("@").split("_")[0].upper()
ACCENT_COLOR = env("ACCENT_COLOR", "#FFD43B")

# 승인 대기 만료 시간
PENDING_EXPIRE_HOURS = int(env("PENDING_EXPIRE_HOURS", "24"))

GITHUB_REPOSITORY = env("GITHUB_REPOSITORY", "owner/repo")
