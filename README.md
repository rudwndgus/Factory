# Pixel Shorts Factory

**웹앱: [https://rudwndgus.github.io/Factory/](https://rudwndgus.github.io/Factory/)** · [소스 코드](https://github.com/rudwndgus/Factory)

## Curiosity Room · AI 이미지 제작

- Settings의 **Visual Source Mode**: `AI First`(기본), `Mixed`, `Real First`.
- **Visual Style Preset** 기본값: cinematic, mysterious, educational, high-contrast, clean, visually striking.
- AI First는 장면별 AI 이미지를 먼저 생성합니다. 실제 인물·공식 뉴스·정확한 제품·공식 사진 등 실제 자료가 필요한 장면은 외부 자료를 요구합니다. 실제 자료를 못 찾으면 AI로 사실을 위조하지 않고 차단합니다.
- Mixed는 실자료 필수 장면 외에 장면 순서에 따라 AI/실자료 우선순위를 번갈아 적용합니다. Real First는 실자료를 먼저 찾고, 필수 실자료 장면이 아닐 때만 AI로 전환합니다.
- 기본 이미지 모델은 `IMAGE_MODEL=gpt-image-2`, 1024×1536 medium 이미지에서 9:16 중앙 크롭합니다. 이미지당 예약액은 `IMAGE_CALL_RESERVATION_USD=0.20`(실청구액 아님). 예산·모델 권한·결제 오류 시 임의 도형으로 대체하지 않습니다.
- 웹앱의 **AI Test run**은 5개 장면의 실제 유료 이미지 생성 + 로컬 음성으로 `Why space is silent`를 만듭니다. 비용 확인창을 거치며 **업로드는 금지**됩니다. 기존 `scripts/test_run.py`는 무료 오프라인 기술 검사로 남겨둡니다.
- Review room에서 장면별 AI/외부 출처, 프롬프트, 스타일을 확인하고 이미지를 개별 재생성할 수 있습니다. 재생성은 비용이 발생하고 MP4·QC를 다시 만듭니다.
- 영상마다 보고서 한 장이 생성되고, 그 안에 10개 부서의 수행 내용과 특이사항이 계속 정리됩니다. 대표가 **확인 완료**를 누르면 보고서는 정리함으로 이동합니다. 작업이 진행되는 동안 사무실 직원 전체가 움직이고, 일시정지·중단 시 멈춥니다. 이미 전송한 외부 요청은 즉시 취소/환불할 수 없습니다.

### 휴대폰과 회사 PC 서버

GitHub 웹앱은 모바일 화면을 지원하지만 휴대폰의 `localhost`는 회사 PC가 아닙니다. **회사 PC를 켜놓기만 해서는 외부 접속이 되지 않습니다.** 현재 서버는 `127.0.0.1:8000`으로 로컬에만 바인딩되어 있습니다.
회사 IT의 허가를 받은 VPN/보안 원격 연결 또는 인증된 HTTPS 서버가 필요합니다. 회사 네트워크의 포트 개방/터널 설치는 별도 승인 후 진행하세요. PC 절전·재부팅·인터넷 단절 시 제작은 중단됩니다. API 키와 `.env`, `data`를 GitHub에 올리지 마세요.

작은 픽셀 사무실로 관리하는 실제 Shorts 제작 서버입니다. 각 사무실은 하나의 채널 작업공간이며, 직원의 상태는 SQLite에 저장된 제작 작업에서 갱신됩니다. Next.js 웹앱과 Python/FastAPI 제작 서버를 분리했습니다.

> **접속 안내:** 위 GitHub Pages 주소는 웹 클라이언트입니다. GitHub Pages에서는 Python·SQLite·FFmpeg 서버를 실행할 수 없습니다. 최초 접속 시 **Connect your server**에서 로컬 또는 HTTPS로 배포한 제작 서버 주소와 owner 비밀번호를 입력하세요. 서버가 연결되지 않으면 OFFLINE과 빈 상태가 표시됩니다. 작업/계정/영상이 기기 사이에 공유되려면 같은 상시 실행 서버에 연결해야 합니다.

## 구현된 흐름

```text
Next.js / Phaser office ── authenticated API + SSE ── FastAPI
                                                         │
                    SQLite jobs / settings / reports ────┤
                                                         │
RSS topic → source evidence → script → storyboard → stills
                                                      → voice → subtitles → FFmpeg → QC
                                                                                    │
                                                            CEO review → YouTube → analytics
```

- 사무실 생성/편집/전환, 언어·제작 시간·타임존·예산·검토 설정
- SQLite 영속 작업 큐, 단계 체크포인트, 재시도와 지수 백오프, 재시작 복구
- 시작·일시정지·중지·유지보수·확인형 긴급 정지, HQ 전체 제어
- 10명의 절차적 픽셀 직원, 클릭 상세, 실시간 상태, 확대/이동, CEO 보고서
- NASA/ScienceDaily RSS 후보 수집, 원본 대본, 장면 구성, 출처/주장 저장
- Wikimedia Commons의 CC0/Public domain 메타데이터 이미지, 선택적 Pexels, 직접 생성한 설명 그래픽 폴백
- OpenAI 텍스트/TTS 어댑터, API 없이 실행 가능한 명시적 offline TEST RUN
- 1080×1920 MP4, Ken Burns 줌, 음성, 구간별 자막, 음량 정규화, 선택적 음악/SFX
- 해상도·오디오·검은 화면·장시간 무음·자막·길이·권리·사실 확인 QC 게이트
- 대본 수정, 단계/장면 재생성 API, 검토/승인/거절, 다운로드
- 서버 측 암호화 자격증명, Google OAuth state 보호, 사무실별 채널 연결
- 공식 YouTube 재개 가능 업로드, 공개/미등록/비공개/예약, 업로드 기록, 통계 수집
- 영상별 통합 제작 보고서, 부서별 작업·특이사항, 확인 대기/정리함, 비용 예약 및 한도 차단
- PWA 매니페스트·아이콘·오프라인 셸, Docker Compose 및 Windows 실행 스크립트

## Windows에서 처음 실행

Python 3.12+, Node.js 22+, PowerShell이 필요합니다. 로컬 FFmpeg 실행 파일은 `imageio-ffmpeg` 패키지에 포함됩니다. TEST RUN의 오프라인 음성은 Windows System.Speech를 사용합니다.

```powershell
cd C:\Users\kjunghyun\Desktop\rud\Kyung\Factory
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

- 웹앱: **http://localhost:3000**
- 서버: **http://localhost:8000**
- API 문서: **http://localhost:8000/docs**
- `setup.ps1`은 `.env`가 없을 때만 고유한 `MASTER_KEY`와 `OWNER_PASSWORD`를 생성합니다.
- 로컬 `.env`의 `OWNER_PASSWORD`를 읽고 웹앱의 연결 창에 입력하세요. Google 비밀번호가 아닙니다.
- `dev.ps1` 종료 시 자신이 시작한 API 프로세스도 종료합니다. 제작을 계속하려면 창을 유지하거나 Docker를 사용하세요.

수동 실행:

```powershell
$env:PYTHONPATH='apps/api'
.\.venv\Scripts\python.exe -m uvicorn factory.main:app --host 127.0.0.1 --port 8000
# 별도 터미널
cd apps/web
npm run dev
```

## 첫 TEST RUN

서버 연결 → Office에서 **Start** → **AI Test run** → 비용 확인 → Production에서 단계 확인 → Video library에서 결과 확인/다운로드.

웹앱의 AI TEST RUN은 고정된 교육용 대본, 실제 AI 이미지, 로컬 합성 음성으로 MP4를 만듭니다. 아래 오프라인 CLI 검사는 자체 그래픽을 사용합니다. 두 테스트 모두 `test_mode`로 저장되고 **절대 업로드할 수 없습니다**. 가짜 조회수나 가짜 업로드 성공을 추가하지 않습니다.

자동화 검증용:

```powershell
$env:PYTHONPATH='apps/api'
.\.venv\Scripts\python.exe scripts/test_run.py
```

이 명령은 별도의 `Acceptance TEST RUN` 사무실을 만들고 결과 경로를 출력합니다. 기존 서버의 worker와 동시에 돌리지 말고 서버를 정지한 상태에서 실행하세요.

## OpenAI 설정

Settings → Integrations에서 `OPENAI_API_KEY` 저장 → Test로 인증 검증. 또는 `.env`에 입력하고 서버를 재시작하세요. 저장소/브라우저 번들에는 키를 넣지 않습니다.

| 환경변수 | 용도 |
| --- | --- |
| `MASTER_KEY` | Fernet 암호화 키. 암호화 DB와 함께 안전하게 백업 |
| `OWNER_PASSWORD` | 소유자 로그인 비밀번호, 16자 이상 |
| `OPENAI_API_KEY` | 실제 대본과 음성 생성 |
| `TEXT_MODEL` | 설정 가능한 텍스트 모델, 기본 `gpt-4.1-mini` |
| `TTS_MODEL`, `TTS_VOICE` | 음성 모델/목소리, 기본 `gpt-4o-mini-tts` / `coral` |
| `TEXT_CALL_RESERVATION_USD` | 텍스트 호출당 보수적 비용 예약, 기본 0.05 |
| `VOICE_CALL_RESERVATION_USD` | 음성 호출당 보수적 비용 예약, 기본 0.03 |
| `GLOBAL_DAILY_BUDGET`, `GLOBAL_MONTHLY_BUDGET` | 전체 사무실의 비용 예약 합계 제한 |
| `DATA_DIR` | SQLite와 렌더 파일 저장 위치 |
| `FFMPEG_PATH` | 별도 FFmpeg 실행 파일 경로, 선택 사항 |
| `ALLOWED_ORIGINS` | 쉼표로 구분한 허용 웹앱 origin |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google 웹 OAuth 클라이언트 |
| `GOOGLE_REDIRECT_URI` | 실제 서버의 OAuth 콜백 URL |
| `PEXELS_API_KEY` | 선택적 사진 검색 |

요금은 모델에 따라 달라집니다. 이 버전의 비용 수치는 **설정한 보수적 예약액**이며 청구서와 동일하지 않습니다. 제공된 토큰 사용량은 대본 기록에 보관합니다. 모델을 변경하면 예약액도 조정하세요. 과금 호출 전에 SQLite 트랜잭션으로 전체/사무실별 일·월 한도를 검사합니다. 실패하거나 응답이 불확실한 호출의 예약액도 유지합니다.

공식 API 참고: [텍스트 생성](https://platform.openai.com/docs/api-reference/chat/create), [음성 생성](https://platform.openai.com/docs/api-reference/audio/createSpeech).

## YouTube OAuth 및 업로드

1. Google Cloud 프로젝트에서 **YouTube Data API v3**, **YouTube Analytics API**를 활성화합니다.
2. OAuth 동의 화면과 웹 애플리케이션 클라이언트를 구성합니다. 테스트 상태에서는 본인 계정을 테스트 사용자로 추가합니다.
3. 리디렉션 URI를 서버의 `/api/youtube/oauth/callback`과 정확히 일치시킵니다. 로컬 기본값은 `http://localhost:8000/api/youtube/oauth/callback`입니다.
4. 클라이언트 ID/Secret을 서버 설정에 저장합니다.
5. 각 Office → Settings → **Connect YouTube**에서 Google 승인을 완료합니다.

채널 이름/ID는 공식 API에서 조회합니다. Refresh token과 access token은 서버에 암호화 저장되며 브라우저에 반환하지 않습니다. 요청 범위는 업로드, 채널 읽기, Analytics 읽기입니다. 결제/계정 비밀번호는 요구하지 않습니다.

일반 제작을 완료한 뒤 출처와 주장을 검토하고 `I verified the facts`를 눌러 사실 검토를 기록하세요. 모든 QC가 통과하고 Review Mode가 켜져 있으면 CEO 승인도 필요합니다. 수동 업로드는 최종 확인을 거칩니다. 자동 업로드는 Office 설정에서 별도로 활성화하며 RUNNING 상태와 동일한 QC/검토 규칙을 요구합니다.

첫 배포 시 일반 영상의 사실 확인은 **자동 인증되지 않습니다**. 모델 자신감만으로 공개하지 않도록 의도적으로 CEO 검증 게이트를 유지했습니다. Google 프로젝트 승인/쿼터/동의 상태에 따라 업로드나 공개가 제한될 수 있습니다. 실제 자격증명을 제공하기 전에는 실계정 OAuth/업로드를 검증하지 않았습니다.

업로드 결과가 불확실하면 새 업로드를 재시도하지 않고 기존 세션의 완료 여부를 조회합니다. 불완전 세션은 수동 조정 대상으로 차단됩니다. [공식 재개 가능 업로드 문서](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol).

## 제어 규칙

| 모드 | 동작 |
| --- | --- |
| RUNNING | 예약 제작, 다음 단계, 승인된 자동 업로드 허용 |
| PAUSED | 진행 중 안전 단계 종료 후 대기. 새 단계/업로드 중지 |
| STOPPED | 대기 작업 취소. 명시적 TEST RUN 가능 |
| MAINTENANCE | 제작/업로드 차단. 설정/보고서/점검 가능 |
| EMERGENCY_STOP | 대기 작업 취소, 후속 외부 호출 차단, 렌더 프로세스 취소 |

이미 제공자에게 전송한 요청이나 업로드 바이트를 되돌릴 수는 없습니다. 서버가 재시작되면 중단된 작업은 저장된 단계에서 복구됩니다. 업로드는 일반 재시도 대상과 분리되어 중복을 방지합니다. SQLite/스케줄러는 **worker 1개**로 실행하세요.

## 이미지·음악·저장소

- 자동 외부 이미지는 Wikimedia의 명시적 CC0/Public domain 기록 또는 설정된 Pexels를 사용합니다. AI First는 실자료 필수 장면을 제외하고 AI 이미지를 먼저 생성합니다. AI 생성 실패를 그래픽으로 대체하지 않습니다. 자체 도형은 명시적 오프라인 기술 검사에만 사용합니다.
- 외부 이미지와 라이선스/출처 기록을 영상에 연결합니다. 사용자 교체 이미지는 기본 UNSAFE로 처리합니다.
- `media_library/music`와 `media_library/sfx`에 오디오와 같은 이름의 JSON 라이선스 파일을 추가할 수 있습니다. 예: `ambient.mp3` + `ambient.json`.
- `rights: CLEARED`와 `license`가 없는 트랙은 선택되지 않습니다. 음악은 낮은 볼륨으로 믹싱합니다.
- 데이터는 `data/factory.sqlite3`, 미디어는 `data/media/<office>/<video>/`에 저장됩니다. 자동 만료 삭제는 하지 않습니다.
- R2는 필수가 아니며 아직 구현하지 않았습니다. Docker의 `factory-data` 볼륨으로 시작하고 볼륨과 `MASTER_KEY`를 함께 백업하세요.

## Docker 및 원격 접속

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
# http://localhost:8080 접속
```

Docker 이미지에는 FFmpeg, Linux 오프라인 TTS(espeak-ng), 폰트가 포함됩니다. Caddy가 웹앱을 제공하고 `/api/` 요청을 backend로 전달합니다. SQLite는 영속 볼륨에 있습니다.

실제 외부 사용은 소유한 VPS/호스트에 Docker Compose를 실행하고 HTTPS reverse proxy를 구성하세요. `ALLOWED_ORIGINS`에 프런트엔드 origin을, `GOOGLE_REDIRECT_URI`에 HTTPS API callback을 설정합니다. GitHub Pages 프런트엔드에서도 그 HTTPS 서버를 연결할 수 있습니다. **현재 저장소에 외부 VPS 계정/도메인이 제공되지 않았으므로 원격 backend는 자동 생성하지 않았습니다.**

GitHub Pages는 `main`에 push하면 Python 테스트와 Next.js 정적 빌드 통과 후 자동 배포됩니다. 페이지 빌드 시 `/Factory` base path가 적용됩니다. API 키는 Actions에 넣을 필요가 없습니다. 키는 실제 서버에만 저장합니다.

## 테스트

```powershell
$env:PYTHONPATH='apps/api'
.\.venv\Scripts\python.exe -m pytest apps/api/tests -q
.\.venv\Scripts\python.exe scripts/test_run.py
cd apps/web
npm run build
# 로컬 API와 Next dev server가 실행 중인 상태에서:
npx playwright test
```

서버 테스트는 상태 전환, pause, emergency stop, 체크포인트, 재시도/백오프, 예산, 중복 탐지, 사무실 분리, 암호화, 보고서, 인증, QC, 모의 업로드/Analytics를 검증합니다. 브라우저 테스트는 실제 로그인, 사무실 생성, 보고서, 제어와 모바일 가로 넘침을 확인합니다. 테스트는 실제 YouTube 게시를 하지 않습니다.

첫 종단 테스트에서 실제 **38.35초 / 1080×1920 MP4**를 생성하고 오디오·자막·해상도·파일·길이·검은 화면·무음 검사를 통과했습니다.

## 현재 한계와 후속 작업

이번 리빌드는 실행/검증 가능한 V1 기반이며, 마스터 프롬프트의 모든 고급 기능이 완성된 상태는 아닙니다.

- 사실 검증은 수집 증거 + 구조화된 주장 + CEO 확인입니다. 독립 다중 출처 자동 교차 검증과 의미 임베딩 중복 탐지는 후속 작업입니다.
- 후보 점수 중 수집하지 않은 신호는 null입니다. 조회수/트렌드 점수/수익을 만들지 않습니다. 카테고리 비중은 저장되지만 적응형 샘플링/자동 전략 변경은 미구현입니다.
- AI 이미지 생성과 Commons/Pexels 선택을 지원합니다. NASA/NOAA/USGS 전문 미디어 어댑터, 생성 이미지의 자동 사실 검증, R2는 후속 작업입니다. AI 생성 표시와 출처 기록은 법적 권리나 시각적 정확성을 보증하지 않습니다.
- TTS는 장면별 실제 음성 길이로 자막 시간을 분할합니다. 단어별 forced alignment와 키워드 하이라이트는 미구현입니다.
- 렌더러는 줌/중앙 crop/컷/자막/음량 정규화/선택적 음악·SFX를 지원합니다. 패럴랙스, 다양한 전환, 스마트 피사체 crop은 후속 작업입니다.
- 일부 장면 교체 기능은 API로 제공되며 관리 UI의 세부 이미지/권리 편집과 로그 고급 필터는 추가 작업이 필요합니다.
- 영상별 보고서는 실제 작업 기록을 한 장에 집계합니다. 학습형 전략 추천/변경/되돌리기, 수익 지표, HQ 전체 통합 비용 통계는 미구현입니다.
- Docker 파일은 제공하지만 이 Windows 세션에서 Docker 엔진을 사용한 검증은 수행하지 않았습니다.

권장 다음 단계: HTTPS 제작 서버 배포 → API/OAuth 설정 → 비공개 실영상 제작/검토/업로드 검증 → 다중 출처 팩트체크와 전략 학습 확장.

## 문제 해결

- **OFFLINE**: API 서버 실행 여부, URL, HTTPS, CORS origin, owner 로그인을 확인하세요.
- **BLOCKED**: 작업 오류 메시지에 표시된 API 설정, 예산 또는 QC 항목을 해결한 뒤 Retry stage.
- **암호 해독 오류**: 기존 DB를 암호화한 `MASTER_KEY`를 복원하세요. 키를 새로 만들면 기존 자격증명을 읽을 수 없습니다.
- **OAuth redirect mismatch**: Google Console과 서버 `GOOGLE_REDIRECT_URI`를 문자 단위로 맞추세요.
- **음성 생성 실패**: TEST RUN에서는 Windows System.Speech 또는 Linux espeak-ng가 필요합니다. 일반 제작에는 OpenAI 설정이 필요합니다.
- **렌더링 실패**: FFmpeg 실행 여부/디스크 용량을 확인하고 `data/media/.../render*.log`를 확인하세요. 영상 길이가 목표 범위를 벗어나면 QC가 게시를 막습니다.

## 구조

```text
apps/api/factory/   configuration, database, security, providers, assets,
                   media, engine, editing, YouTube, reports, API
apps/api/tests/     isolated backend tests
apps/web/           Next.js, React, Phaser, PWA, browser tests
packages/shared/    API contract notes
scripts/            setup, dev, Docker start/stop, offline test run
docker/             backend/frontend images and reverse proxy
media_library/      rights-cleared optional audio
data/               private runtime data (gitignored)
```
