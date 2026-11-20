# 연예뉴스 → 카드뉴스 → 텔레그램 승인 → 인스타 자동 업로드

비용 **0원** 구성. 서버 없이 GitHub Actions에서 전부 돌아감.

```
[6시간마다]  네이버 뉴스 수집 → Gemini 선별·요약 → 카드뉴스 5장 생성 → 텔레그램으로 미리보기 + 버튼
[10분마다]   텔레그램 버튼 확인 → ✅ 누른 건 인스타 캐러셀 업로드 → 완료 링크 회신
```

| 역할 | 서비스 | 비용 |
|---|---|---|
| 실행 서버/스케줄 | GitHub Actions (public 저장소) | 무료 |
| 뉴스 수집 | 네이버 검색 API | 무료 (하루 25,000회) |
| 선별·요약·캡션 | Gemini API 무료 티어 | 무료 (회차당 2회 호출) |
| 카드 이미지 | 자체 생성 (Pillow) | 무료 |
| 이미지 호스팅 | 저장소 + jsDelivr CDN | 무료 |
| 승인 | 텔레그램 봇 | 무료 |
| 업로드 | Instagram 공식 API | 무료 |

---

## 세팅 순서 (약 30~40분)

### 1. GitHub 저장소 만들기
1. github.com 로그인 → 우측 상단 `+` → **New repository**
2. 이름 예: `news-insta-bot` / **Public** 선택 (무료 무제한 실행 + 이미지 호스팅 때문. API 키는 Secrets에 들어가서 외부에 안 보임)
3. 생성 후 **uploading an existing file** 클릭 → 이 폴더 안의 파일 전부 드래그 → Commit
   - `.github` 폴더가 꼭 같이 올라가야 함 (숨김 폴더라 안 보이면 GitHub Desktop 사용)

### 2. 네이버 검색 API 키
1. developers.naver.com → **Application → 애플리케이션 등록**
2. 사용 API: **검색** / 환경: WEB, URL은 `http://localhost` 아무거나
3. `Client ID`, `Client Secret` 복사

### 3. Gemini API 키
1. aistudio.google.com → **Get API key → Create API key**
2. 키 복사 (결제 등록 안 하면 무료 티어로만 동작 → 과금될 일 없음)

### 4. 텔레그램 승인 봇
1. 텔레그램에서 **@BotFather** → `/newbot` → 이름 지정 → **토큰** 복사
   - ⚠️ OpenClaw 봇과 **반드시 다른 새 봇**으로 (같은 봇 쓰면 메시지 수신이 충돌함)
2. 만든 봇에게 아무 메시지나 보내기
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 열기 → `"chat":{"id":123456789` 숫자가 **chat id**

### 5. 인스타그램 API
1. 인스타 앱 → 설정 → 계정 유형 → **프로페셔널 계정(비즈니스 또는 크리에이터)** 으로 전환
2. developers.facebook.com → **내 앱 → 앱 만들기** → 사용 사례에서 **Instagram 관리(Manage messaging & content on Instagram)** 선택
3. 앱 대시보드 → Instagram → **API setup with Instagram login**
   - "Generate access tokens"에서 **계정 추가** → 인스타 로그인 → 권한 허용
   - 표시되는 **액세스 토큰**과 **Instagram 사용자 ID(IG_USER_ID)** 복사
   - 권한에 `instagram_business_content_publish` 포함 확인
4. 본인 계정(앱에 역할로 추가된 계정)에 올리는 용도라 앱 검수(App Review) 없이 개발 모드로 사용 가능

### 6. GitHub에 키 등록
저장소 → **Settings → Secrets and variables → Actions**

**Secrets** 탭 (New repository secret):

| 이름 | 값 |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 Client Secret |
| `GEMINI_API_KEY` | Gemini 키 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | chat id 숫자 |
| `IG_USER_ID` | 인스타 사용자 ID |
| `IG_ACCESS_TOKEN` | 인스타 액세스 토큰 |
| `GH_PAT` | (선택) 토큰 자동 교체용 — 아래 참고 |

**Variables** 탭 (선택, 안 넣으면 기본값):

| 이름 | 예시 | 설명 |
|---|---|---|
| `BRAND_HANDLE` | `@my_kent_news` | 카드 하단 계정명 |
| `BRAND_NAME` | `연예 브리핑` | 카드 상단 브랜드명 |
| `ACCENT_COLOR` | `#FF3D6E` | 포인트 색 |
| `NEWS_QUERIES` | `연예,아이돌 컴백,드라마 출연` | 수집 키워드 (쉼표 구분) |
| `ITEMS_PER_POST` | `4` | 한 게시물당 기사 수 (최대 9) |
| `GEMINI_MODEL` | `gemini-flash-latest` | 모델 바뀌면 AI Studio에서 무료 Flash 모델명 확인 후 입력 |
| `IG_AI_LABEL` | `true` | 인스타 'AI 정보' 라벨 표시 여부 |

### 7. 실행
1. 저장소 → **Actions** 탭 → 워크플로 활성화 버튼 클릭
2. **1) 뉴스 수집 & 승인 요청** → **Run workflow** 로 수동 테스트
3. 1~2분 뒤 텔레그램에 카드 5장 + 버튼 도착 → ✅ 누르기
4. 10~20분 안에 인스타 업로드되고 텔레그램으로 링크 옴

이후엔 자동: 한국시간 **07:17 / 13:17 / 19:17 / 01:17** 에 승인 요청이 옴.
(새벽 1시 건은 아침에 눌러도 됨. 24시간 지나면 자동 만료)

---

## 알아둘 것

- **승인 반영 지연**: GitHub 스케줄은 10분 간격이지만 혼잡하면 더 늦어짐. 급하면 Actions → `2) 승인 확인` → Run workflow.
- **인스타 토큰 60일 만료**: 7일마다 자동 연장함. 토큰 문자열이 바뀌면
  - `GH_PAT` 등록해 뒀으면 자동 교체 (GitHub → Settings → Developer settings → Fine-grained token, 이 저장소에 `Secrets: Read and write` 권한)
  - 없으면 텔레그램으로 새 토큰을 보내주니 Secrets에 직접 붙여넣기
- **콘텐츠 원칙**: 보도사진·기사 원문을 사용하지 않고, 자체 카드 디자인과 AI 재작성 요약에 출처 표기. 루머·사생활·미확정 의혹 기사는 프롬프트에서 제외. 그래도 최종 확인은 승인 버튼 누르기 전에 직접.
- **저장소 용량**: 처리 끝난 이미지는 자동 삭제. 다만 git 기록은 남아서 1년에 수백 MB 정도 쌓임 → 문제 되면 저장소 새로 만들면 됨.

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 텔레그램에 아무것도 안 옴 | Actions 로그에서 `1)` 실행 결과 확인. `설정 누락` 메시지면 Secrets 이름 오타 |
| `Gemini 호출 실패` / 404 | `GEMINI_MODEL` 변수에 AI Studio에 있는 현재 Flash 모델명 입력 |
| 버튼 눌러도 반응 없음 | OpenClaw 등 다른 곳에서 같은 봇 토큰 쓰는지 확인. `2)` 워크플로 로그 확인 |
| `컨테이너 ... ERROR` | Variables에 `IMAGE_HOST` = `raw` 넣고 재시도 |
| 인스타 업로드 권한 오류 | 프로페셔널 계정인지, 토큰에 `instagram_business_content_publish` 권한 있는지 |

## 파일 구조
```
bot/collect.py      6시간 작업 (수집→요약→카드→텔레그램)
bot/approve.py      10분 작업 (버튼 확인→업로드, 만료·토큰 연장)
bot/news.py         네이버 뉴스 수집·본문 추출
bot/summarize.py    Gemini 선별·요약 프롬프트  ← 톤/규칙 바꾸려면 여기
bot/render.py       카드 디자인                ← 디자인 바꾸려면 여기
bot/instagram.py    인스타 업로드
bot/telegram.py     텔레그램 봇
state/              진행 상태 (자동 관리, 건드리지 말 것)
```
