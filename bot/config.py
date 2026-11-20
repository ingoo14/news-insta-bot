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


def env(name, default=None, required=False):
    v = os.environ.get(name) or default  # GitHub Variables가 비어 있으면 "" 로 들어와서 기본값 처리
    if required and not v:
        raise SystemExit(f"[설정 누락] 환경변수 {name} 가 비어 있음 (GitHub Secrets 확인)")
    return v


# 뉴스 수집 (네이버 검색 API — 무료, 하루 25,000회)
NAVER_CLIENT_ID = env("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = env("NAVER_CLIENT_SECRET")
NEWS_QUERIES = [q.strip() for q in env("NEWS_QUERIES", "연예,아이돌 컴백,드라마 출연,가수 신곡,예능").split(",") if q.strip()]
NEWS_LOOKBACK_HOURS = int(env("NEWS_LOOKBACK_HOURS", "8"))
ITEMS_PER_POST = int(env("ITEMS_PER_POST", "4"))

# 요약 (Gemini 무료 티어)
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-flash-latest")

# 텔레그램 (승인용 — OpenClaw와 별개의 새 봇 사용)
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
ACCENT_COLOR = env("ACCENT_COLOR", "#FF3D6E")

# 승인 대기 만료 시간
PENDING_EXPIRE_HOURS = int(env("PENDING_EXPIRE_HOURS", "24"))

GITHUB_REPOSITORY = env("GITHUB_REPOSITORY", "owner/repo")
