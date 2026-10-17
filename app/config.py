from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_name: str = "ReMory Auth API"
    debug: bool = True

    # Database
    database_url: str = "postgresql+psycopg2://remory:remory@localhost:5432/remory"

    # JWT
    jwt_secret: str = "dev-only-change-me-in-production-0000000000"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 30
    refresh_token_ttl_days: int = 30
    register_token_ttl_min: int = 10

    # WebAuthn
    rp_id: str = "remory.app"
    rp_name: str = "ReMory"
    webauthn_origins: list[str] = ["https://remory.app", "http://localhost"]
    webauthn_timeout_ms: int = 60000

    # 앱-도메인 연결(Associated Domains / Digital Asset Links).
    # 실기기 패스키 테스트 시 rp_id 도메인이 아래 파일들을 HTTPS로 서빙해야 한다.
    # iOS: "TEAMID.com.example.remory" 형식(Apple Developer 팀ID + 번들ID)
    ios_app_ids: list[str] = []
    # Android: 앱 패키지명과 서명 인증서 SHA-256 지문(colon 구분 hex)
    android_package_name: str = ""
    android_sha256_fingerprints: list[str] = []

    # 전화번호 인증(OTP)
    otp_ttl_sec: int = 180
    otp_max_attempts: int = 5

    # 리포트 문구 생성(Groq). 키가 없으면 규칙 기반 문구로 물러난다.
    groq_api_key: str = ""
    # 리포트 문구를 만들 때 한 번 부르고 이만큼 쉰다.
    #
    # Groq 무료 등급은 분당 출력 토큰(OTPM)이 정해져 있어, 잇따라 부르면
    # 뒤엣것이 429 로 막힌다. 어르신이 여럿이면 배치가 그만큼 여러 번 부르므로
    # 첫 사람 것만 모델이 쓰고 나머지는 규칙 기반으로 떨어진다.
    #
    # 리포트는 밤에 도는 배치라 빨리 끝날 이유가 없다. 쉬어 가며 부른다.
    # 등급을 올렸으면 0 으로 두면 된다.
    llm_min_gap_sec: float = 65.0
    llm_model: str = "qwen/qwen3.8-27b"

    # 알림 생성
    # 부정 감정이 이 횟수만큼 연달아 기록되면 긴급 알림을 만든다.
    emotion_alert_streak: int = 3
    # 같은 종류 알림을 이 시간 안에는 다시 만들지 않는다(쿨다운).
    emotion_alert_cooldown_min: int = 60
    chat_alert_cooldown_min: int = 10
    # 신호가 오락가락할 때 재연결 알림이 반복되지 않게.
    reconnect_alert_cooldown_min: int = 30

    # 가족 초대 코드
    # 가족에게 코드를 알려주고 바로 쓰게 하려는 값이라 길게 둘 이유가 없다.
    # 아래 create_invite_code 가 24시간을 넘지 않도록 한 번 더 깎는다.
    invite_code_ttl_hours: int = 24

    # 인형(Device)
    # 마지막 heartbeat 이후 이 시간이 지나면 '연결 끊김'으로 본다.
    device_offline_after_sec: int = 600
    # 완충 시 예상 사용 시간(배터리 잔여 시간 표시용).
    device_battery_full_hours: int = 18

    # 서비스 정보(GET /service/info)
    service_version: str = "1.0.2"
    service_min_supported_version: str = "1.0.0"
    terms_url: str = "https://remory.app/terms"
    privacy_url: str = "https://remory.app/privacy"
    support_email: str = "support@remory.app"
    support_phone: str = ""

    # 업로드 저장소: local | s3
    # 컨테이너 배포에서는 로컬 디스크가 재시작 시 사라지므로 운영은 s3 를 쓴다.
    storage_backend: str = "local"
    s3_bucket: str = ""
    # 배포 대상 리전(오리건). presigned URL 이 이 리전 엔드포인트로 발급되므로
    # 버킷 리전과 반드시 같아야 한다.
    s3_region: str = "us-west-2"
    # 버킷 안에서 쓸 상위 폴더(예: "prod"). 비워두면 버킷 루트에 올린다.
    s3_key_prefix: str = ""
    # CloudFront·커스텀 도메인을 앞에 둘 때만 채운다.
    # 비우면 https://<버킷>.s3.<리전>.amazonaws.com 을 쓴다.
    s3_public_base_url: str = ""
    # 버킷을 비공개로 두고 조회 응답에만 만료되는 presigned URL 을 내려준다.
    # 끄면 버킷을 퍼블릭 읽기로 열어야 앱이 파일을 받을 수 있다.
    s3_presign: bool = True
    s3_presign_ttl_sec: int = 3600

    # Fish Audio 보이스 클로닝. 녹음본으로 비공개 목소리 모델을 만든다.
    # 비워두면 등록을 건너뛴다(녹음만 받고 status=training 유지 — 데모/미연동).
    fish_api_key: str = ""
    # 모델 생성·조회·삭제 호출 타임아웃(초). 업로드와 잡음 제거가 있어 여유를 둔다.
    fish_timeout_sec: int = 60

    # SMS provider: mock | solapi | aligo | ncp
    sms_provider: str = "mock"
    # 솔라피(구 CoolSMS)
    solapi_api_key: str = ""
    solapi_api_secret: str = ""
    # 알리고 테스트 모드. 실제 발송·과금 없이 요청만 검증한다.
    aligo_test_mode: bool = False
    # Firebase 전화번호 인증. 앱이 Firebase 로 인증하고 서버는 ID 토큰만 검증한다.
    # 문자를 구글이 보내므로 발신번호 등록·사업자등록이 필요 없다.
    # 비우면 /auth/phone/verify-firebase 가 401 을 낸다.
    firebase_project_id: str = ""

    # FCM 푸시 발송. 서비스 계정 키가 있어야 보낼 수 있다(검증과 달리 공개키로
    # 안 된다). 둘 다 비어 있으면 푸시를 보내지 않고 알림만 쌓는다.
    # 파일 경로를 먼저 보고, 없으면 JSON 내용을 그대로 읽는다.
    fcm_service_account_file: str = ""
    fcm_service_account_json: str = ""

    # 테스트용 번호. 여기 적힌 번호는 문자를 보내지 않고 응답에 인증번호를
    # 그대로 담아 준다. 발신번호 심사를 기다리는 동안 실기기·시연을 돌리려는
    # 것이다. 팀이 가진 번호만 적을 것 — 적힌 번호는 누구나 코드를 받아갈 수 있다.
    otp_test_phone_numbers: list[str] = []

    # 인증번호 재발송 제한(같은 번호 기준). 실제 발송은 건당 요금이 나가고,
    # 이 엔드포인트는 인증 없이 열려 있어서 막지 않으면 잔액이 그대로 샌다.
    otp_send_cooldown_sec: int = 60
    otp_send_max_per_hour: int = 5
    sms_sender_number: str = ""
    aligo_api_key: str = ""
    aligo_user_id: str = ""
    ncp_access_key: str = ""
    ncp_secret_key: str = ""
    ncp_service_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
