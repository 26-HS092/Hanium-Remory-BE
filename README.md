<div align="center">

# 🧸 ReMory Backend

**치매 어르신과 가족을 잇는 돌봄 인형 서비스, 리모리(ReMory)의 백엔드 서버**

인형 *모리* 는 어르신 곁에서 말벗이 되고,
가족은 앱으로 어르신의 하루를 지켜봅니다.

<br/>

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-RDS-4169E1?logo=postgresql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?logo=sqlalchemy&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-EC2%20%C2%B7%20RDS%20%C2%B7%20S3-FF9900?logo=amazonaws&logoColor=white)
![WebAuthn](https://img.shields.io/badge/Auth-Passkey%20(WebAuthn)-000000?logo=webauthn&logoColor=white)
![Claude](https://img.shields.io/badge/LLM-Claude%20%C2%B7%20Groq-D97757?logo=anthropic&logoColor=white)
![Fish Audio](https://img.shields.io/badge/TTS-Fish%20Audio-1E1E1E)
![Firebase](https://img.shields.io/badge/Push-Firebase%20FCM-FFCA28?logo=firebase&logoColor=black)

</div>

---

## 📖 이런 서비스입니다

치매를 앓는 어르신은 하루 대부분을 혼자 보냅니다. 가족은 멀리 있고,
"오늘 어떠셨어요?" 를 물어볼 방법이 전화밖에 없습니다.

**리모리는 어르신 곁에 인형을 둡니다.** 인형 *모리* 는 어르신과 대화를 나누고,
가족이 남긴 메시지를 **가족의 목소리로** 읽어주고, 약 드실 시간을 알려줍니다.
그 사이에 쌓인 대화·감정·활동은 가족 앱의 홈 화면과 하루 리포트로 정리되어 전달됩니다.

<table>
<tr>
<td width="33%" valign="top">

### 👵 어르신
말벗이 되어주는 인형 **모리**.
"모리야" 하고 부르면 대답하고,
사진 속 추억을 함께 이야기하고,
약 시간을 챙겨줍니다.

</td>
<td width="33%" valign="top">

### 🧸 인형 (Device)
Raspberry Pi 5 + AI HAT+ 2(Hailo-10H).
음성 인식·감정 인식은 기기 안에서 하고,
대화·감정·활동을 서버에 올리고,
가족 메시지와 어르신 정보(RAG)를
서버에서 받아 갑니다.

</td>
<td width="33%" valign="top">

### 👨‍👩‍👧 가족 (보호자)
Flutter 앱으로 어르신 상태를 보고,
대화방에 메시지·사진을 남기고,
자기 목소리를 등록하고,
데일리 리포트를 받습니다.

</td>
</tr>
</table>



---

## 🗺 시스템 구성

<div align="center">
  <img src="assets/ReMory_system_architecture_diagram.png" alt="ReMory 서비스 구성 요약" width="100%" />
  <sub>한눈에 보는 구성 — 엣지 디바이스 · AI/외부 API · AWS 클라우드 · 보호자 앱</sub>
  <br/><br/>
  <img src="assets/ReMory_system_architecture.png" alt="ReMory 상세 아키텍처" width="100%" />
  <sub>상세 아키텍처 — 컨테이너 · 배치 · 외부 연동 · CI/CD</sub>
</div>

<br/>

| | 무엇을 하나 | 핵심 |
|---|---|---|
| **① 엣지 · 인형 모리** | 어르신과 직접 대화 | Raspberry Pi 5 + AI HAT+ 2(Hailo-10H) · 카메라/마이크/스피커/LCD/mmWave<br/>웨이크워드 '모리야' → STT(Whisper-base, Hailo NPU) → 얼굴(YuNet)·5감정 분류 → 안전 판별(규칙 → KoELECTRA → LLM)을 **기기 안에서** 처리<br/>추억·가족 정보는 기기 안 Chroma DB 로 RAG |
| **② AI 처리 · 외부 API** | 이해하고 목소리를 만든다 | **LLM** — 대화: Groq gpt-oss-120b (Claude haiku-5-5 로 전환 가능) · 리포트 문구: Groq qwen3.8-27b / **Claude opus-5-5**<br/>**Fish Audio** — TTS 스트리밍(인형이 직접 호출) · 가족 목소리 클로닝(백엔드가 등록)<br/>선택: NAVER CLOVA STT · CosyVoice2 자체 GPU 서버(Tailscale) |
| **③ AWS 클라우드** | 📍 **이 저장소** | EC2 위 Docker Compose(Caddy → FastAPI) · 패스키 인증 · RDS PostgreSQL · S3 · 리포트 배치 |
| **보호자 앱** | 가족이 보는 화면 | Flutter (iOS·Android) → HTTPS REST + Bearer JWT · Firebase(SMS 인증 · FCM 푸시) |

**인형 ↔ 서버가 주고받는 것**

- ⬆️ **올리기** — `X-Device-Token` 으로 감정·발화·활동·안전 이벤트를 올립니다.
- 🔄 **받아 가기** — 가족 메시지·복약·인형 설정을 폴링합니다. 설정(목소리·볼륨 등)은 **30초마다** 확인해 앱에서 바꾼 목소리가 곧바로 반영됩니다.
- 🧠 **RAG 동기화** — 추억·가족 정보 원본과 사진 presigned URL 을 300초마다 받아 기기 안 Chroma DB 를 증분 갱신합니다.
- 🔊 **음성** — 인형은 앱에서 고른 가족 목소리의 **Fish 모델 ID** 로 Fish Audio 에 직접 TTS 를 요청합니다.

> 🔒 **얼굴 원본은 엣지에서 즉시 폐기하고 감정 라벨만 서버로 보냅니다.**
> 어르신 얼굴 이미지는 네트워크를 타지 않습니다.
>
> 🗑 **인형과 나눈 대화는 리포트를 만드는 동안만 보관합니다.** 주간 리포트까지
> 만들고 나면 그 주 발화를 지웁니다(길어야 8일). 보호자 앱으로는 내보내지 않고,
> 리포트에 옮겨 담은 몇 대목만 남습니다.

**설계에서 신경 쓴 것**

- 🔐 **비밀번호가 없습니다.** 어르신 가족은 대체로 비밀번호 관리에 익숙하지 않습니다. 패스키(Face ID·지문)로 가입하고 로그인합니다.
- 🎭 **두 종류의 클라이언트, 두 종류의 인증.** 사람은 JWT, 인형은 기기 토큰(`X-Device-Token`)을 씁니다. 인형이 탈취돼도 가족 계정은 무사하고, 토큰은 언제든 재발급해 즉시 교체할 수 있습니다.
- 👨‍👩‍👧‍👦 **한 어르신, 여러 가족.** 딸도 아들도 같은 어르신에 연결됩니다. 초대 코드로 가족을 부르고, 주보호자가 구성원을 관리합니다.
- 🙈 **내 가족이 아니면 알려주지 않습니다.** 권한 없는 어르신·인형·약은 **404** 로 응답합니다.
- 🩹 **외부 서비스가 죽어도 서비스는 삽니다.** LLM 호출이 실패하면 규칙 기반 문구로, Fish Audio 가 설정되지 않았으면 녹음만 받아 두고 상태만 남깁니다.
- 🔁 **LLM 은 환경 변수 하나로 바꿉니다.** `LLM_MODEL=claude` 면 Claude, 그 외에는 Groq 를 씁니다. 코드는 그대로입니다.

---

## 🧰 기술 스택

| 영역 | 사용 기술 | 메모 |
|---|---|---|
| **웹 프레임워크** | FastAPI + Uvicorn | 자동 생성되는 Swagger 문서(`/docs`) |
| **DB** | PostgreSQL (RDS) · SQLAlchemy 2.0 ORM | 로컬은 SQLite 로도 실행 가능 |
| **마이그레이션** | Alembic | 컨테이너 기동 시 `alembic upgrade head` 선행 |
| **인증** | WebAuthn 패스키 + JWT(access/refresh 로테이션) | 기기는 별도 `X-Device-Token` |
| **파일 저장** | S3 (비공개 버킷 + presigned URL) / 로컬 디스크 | `STORAGE_BACKEND` 로 교체 |
| **음성 합성** | Fish Audio (보이스 클로닝, 비공개 모델) | 모델 ID 를 `speaker_id` 로 저장 · fish.audio 와 삭제 양방향 동기화 |
| **LLM** | Claude (opus-5-5) / Groq (qwen3.8-27b) | 리포트 요약·제안·대화 발췌. `LLM_MODEL` 로 교체 |
| **문자 인증** | Firebase / mock | `SMS_PROVIDER` 로 교체 |
| **푸시 알림** | Firebase Cloud Messaging | 알림 생성 즉시 보호자 폰으로 발송 |
| **DNS · HTTPS** | sslip.io + Caddy(Let's Encrypt) | EC2 공인 IP 로 도메인 · 인증서 자동 |
| **배포** | Docker Compose · Caddy · EC2 · systemd timer | HTTPS 자동, 자동 배포·리포트 배치 |
| **CI** | GitHub Actions (pytest on SQLite) | main push · 모든 PR |

---

## ✨ 주요 기능

| | |
|---|---|
| 🔐 **비밀번호 없는 가입·로그인** | 패스키(Face ID·지문) + 전화번호 인증. 비밀번호를 만들지도, 외우지도 않습니다 |
| 🏠 **홈 대시보드** | 연결 상태 · 대화 중 여부 · 배터리 · 현재 감정 · 감정 추이 · 활동 타임라인 · 안 읽은 알림을 한 화면에 보여줍니다 |
| 💬 **가족 대화방** | 가족이 남긴 글과 사진을 인형이 화면에 띄우고 음성으로 읽어줍니다 |
| 📷 **추억** | 사진 + 시기 + 이야기를 등록하면 인형이 RAG 컨텍스트로 받아가 어르신과 함께 회상합니다 |
| 🎙 **가족 목소리** | 녹음 한 번으로 Fish Audio 에 목소리를 클로닝해 인형이 딸의 목소리로 말합니다. Fish 에 이미 있는 목소리 모델도 ID(또는 주소)로 가져와 고를 수 있습니다. 클로닝이 끝나면 푸시로 알려주고, 앱에서 지우면 Fish 모델도 지우고 fish.audio 에서 지우면 앱 목록에서도 빠집니다 |
| 🔔 **알림** | 부정 감정이 이어질 때, 연결이 끊겼다 돌아올 때, 새 메시지가 올 때 처럼 사건이 생기면 서버가 생성하고, FCM 으로 보호자 폰에 바로 띄웁니다 |
| 📊 **데일리 리포트** | 하루의 대화·감정·활동을 집계하고, 요약과 제안 문구는 LLM 이 씁니다. 그날 나눈 대화까지 재료로 넣어 씁니다 |
| 📈 **주간 리포트** | 한 주(월~일)의 데일리를 모아 대화·감정·긴급 알림 흐름을 정리합니다 |
| ⚙️ **인형 설정** | 이름 · 볼륨 · 기본 목소리 · 방해 금지 시간 · 약 복용 시간 등을 설정할 수 있습니다. 인형은 30초마다 설정을 받아 반영합니다 |
| 👨‍👩‍👧‍👦 **가족 초대** | 초대 코드로 다른 가족을 부르고, 주보호자가 구성원을 관리합니다 |

---

## 🚀 빠른 시작

```bash
git clone https://github.com/Hanium-Remory/backend.git
cd backend

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # 개발은 기본값 그대로도 실행됩니다
alembic upgrade head          # 표 생성·변경은 여기서 합니다
uvicorn app.main:app --reload --port 8000
```

- 📘 **API 문서** — http://localhost:8000/docs (전체 엔드포인트와 스키마)
- 💚 **헬스체크** — `GET /health` (DB 까지 확인합니다)
- 📱 **인증번호** — mock 모드에서는 서버 로그에 찍힙니다
- 🌱 **샘플 데이터** — `POST /dev/seed` (`DEBUG=true` 일 때만)

> DB 없이 흐름만 보려면 `.env` 의 `DATABASE_URL` 을 `sqlite:///./dev.db` 로 바꾸면 됩니다.
> 설정값 전체와 설명은 [`.env.example`](.env.example) 에 있습니다.

**Docker**

```bash
docker compose up --build                                                      # 개발
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build   # 운영
```

**테스트**

```bash
pytest        # SQLite + WebAuthn 목킹으로 전체 플로우 검증
```

---

## 📁 프로젝트 구조

```
app/
├── main.py        FastAPI 앱 + 라우터 등록 + 헬스체크
├── config.py      환경설정 (.env)
├── models.py      SQLAlchemy 모델
├── deps.py        인증 의존성 (보호자 JWT / 기기 토큰)
├── errors.py      공통 응답 봉투 + 예외 핸들러
├── routers/       인증 · 계정/가족 · 인형 · 홈/콘텐츠 · 기록
└── services/      권한검사 · SMS · 저장소(S3) · Fish Audio(목소리 등록) · 목소리 동기화
                   · LLM(Claude/Groq) · 알림 생성 · FCM 푸시

migrations/  Alembic 리비전      scripts/  리포트 배치 · 유지보수
deploy/      systemd 유닛        tests/    pytest
```

---

## 🚢 배포

EC2 한 대 위에서 Caddy 가 HTTPS 를 붙이고 뒤로 API 를 프록시합니다.
DB 는 RDS, 사진·음성 파일은 S3 비공개 버킷 + presigned URL 을 씁니다.
도메인은 sslip.io 로 EC2 공인 IP 에 붙이고, 인증서는 Caddy 가 알아서 발급·갱신합니다.
`deploy/` 의 systemd timer 가 자동 배포와 리포트 배치를 돌립니다.

- **자동 배포** — EC2 가 1분마다 main 을 확인해 바뀌었으면 ff-only merge → `docker compose up -d --build` 합니다. 테스트는 GitHub Actions 가, 배포는 EC2 가 스스로 pull 해서 하므로 SSH 인바운드가 필요 없습니다.
- **리포트 배치** — 데일리는 매일 한국 시간 00:10, 주간은 월요일 00:30 에 지난주(월~일)를 만듭니다.
  주간이 리포트를 만들고 나면 그 주 발화를 지웁니다.
