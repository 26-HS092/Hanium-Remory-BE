import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Protector(Base):
    """보호자 계정."""

    __tablename__ = "protectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Face-ID-first 가입에서는 패스키 등록 시점엔 번호가 없으므로 nullable.
    # 전화번호 인증 단계에서 채워진다. (unique은 NULL 다중 허용)
    phone_number: Mapped[Optional[str]] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(50), default="보호자")
    # 어르신과의 관계(딸/아들/며느리/사위/손주/기타). 가족 멤버 목록에 표시된다.
    relation: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    profile_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # WebAuthn user handle (user.id). 전화번호와 무관한 안정적 식별자.
    user_handle: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    credentials: Mapped[List["Credential"]] = relationship(
        back_populates="protector", cascade="all, delete-orphan"
    )
    notification_setting: Mapped[Optional["NotificationSetting"]] = relationship(
        back_populates="protector", cascade="all, delete-orphan", uselist=False
    )
    family_members: Mapped[List["FamilyMember"]] = relationship(
        back_populates="protector", cascade="all, delete-orphan"
    )


class Credential(Base):
    """등록된 패스키(WebAuthn credential)."""

    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, index=True)  # base64url
    public_key: Mapped[bytes] = mapped_column(LargeBinary)  # COSE public key
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    protector: Mapped["Protector"] = relationship(back_populates="credentials")


class PhoneVerification(Base):
    """전화번호 인증(OTP) 발송 기록."""

    __tablename__ = "phone_verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(6))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebAuthnChallenge(Base):
    """등록/로그인 ceremony 동안 발급한 challenge 임시 저장."""

    __tablename__ = "webauthn_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # base64url
    ceremony: Mapped[str] = mapped_column(String(20))  # registration | authentication
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    user_handle: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    """발급된 refresh token (회수/로테이션용)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), index=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationSetting(Base):
    """보호자가 받을 알림 종류. 없으면 기본값으로 간주한다."""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), unique=True, index=True
    )

    # 프로필 화면의 상위 4종
    urgent: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_report: Mapped[bool] = mapped_column(Boolean, default=True)
    chat: Mapped[bool] = mapped_column(Boolean, default=True)
    marketing: Mapped[bool] = mapped_column(Boolean, default=False)

    # 알림 설정 화면의 세부 항목 (긴급)
    emotion_change: Mapped[bool] = mapped_column(Boolean, default=True)
    device_disconnected: Mapped[bool] = mapped_column(Boolean, default=True)
    medication_missed: Mapped[bool] = mapped_column(Boolean, default=True)
    # (일상)
    voice_request: Mapped[bool] = mapped_column(Boolean, default=True)
    message_delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_training_completed: Mapped[bool] = mapped_column(Boolean, default=True)
    # (리포트/기타)
    weekly_report: Mapped[bool] = mapped_column(Boolean, default=True)
    app_update: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    protector: Mapped["Protector"] = relationship(back_populates="notification_setting")


class PushToken(Base):
    """보호자 폰의 FCM 등록 토큰.

    한 보호자가 폰을 여러 대 쓸 수 있어 여러 줄이 생긴다. 토큰은 기기마다
    하나뿐이라 unique 로 두는데, 폰을 물려주거나 다른 계정으로 다시 로그인하면
    같은 토큰이 주인만 바뀌어 다시 올라온다. 그때는 주인을 옮긴다.
    """

    __tablename__ = "push_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), index=True
    )
    # FCM 등록 토큰. 길이가 정해져 있지 않고 길어질 수 있어 넉넉히 잡는다.
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(10), default="android")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # 앱이 뜰 때마다 갱신한다. 오래된 토큰을 정리할 때 쓴다.
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Base):
    """돌봄 대상(어르신). 인형·가족 멤버가 이 사용자에 묶인다."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    gender: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # female | male
    birth_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    note: Mapped[str] = mapped_column(String(500), default="")  # 좋아하는 것들 등 자유 메모
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    family_members: Mapped[List["FamilyMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    devices: Mapped[List["Device"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    invite_codes: Mapped[List["InviteCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class FamilyMember(Base):
    """보호자 ↔ 어르신 연결. 한 어르신을 여러 보호자가 함께 돌본다."""

    __tablename__ = "family_members"
    __table_args__ = (UniqueConstraint("user_id", "protector_id", name="uq_family_user_protector"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), index=True
    )
    # 주보호자. 가족 멤버 제거 권한을 가진다.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="family_members")
    protector: Mapped["Protector"] = relationship(back_populates="family_members")


class Device(Base):
    """모리 인형."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(30), default="모리")
    serial: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    # 기기가 서버로 데이터를 올릴 때 쓰는 X-Device-Token.
    device_token: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    battery_level: Mapped[int] = mapped_column(Integer, default=100)
    volume: Mapped[int] = mapped_column(Integer, default=70)
    # voices.id 참조. devices ↔ voices 순환 FK를 피하려고 제약은 걸지 않는다.
    default_voice_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "약 드셨어요?" 하고 인형이 복용 여부를 확인할지.
    medication_check: Mapped[bool] = mapped_column(Boolean, default=True)
    # 어르신이 인형과 실제로 대화 중인지(웨이크워드~대화 종료). 인형이 갱신한다.
    in_conversation: Mapped[bool] = mapped_column(Boolean, default=False)
    last_heartbeat_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="devices")
    voices: Mapped[List["Voice"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    dnd: Mapped[Optional["DndSetting"]] = relationship(
        back_populates="device", cascade="all, delete-orphan", uselist=False
    )
    medications: Mapped[List["Medication"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class Voice(Base):
    """인형이 낼 수 있는 목소리(가족 목소리 학습 결과 또는 기본 음성)."""

    __tablename__ = "voices"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    # 기본 음성은 특정 보호자에 속하지 않으므로 nullable.
    protector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("protectors.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready | training | failed
    progress: Mapped[int] = mapped_column(Integer, default=100)
    # 가족이 업로드한 녹음 파일 주소. 보이스 클로닝 학습의 원본이며,
    # 기본 음성(학습본이 아닌 것)은 녹음 파일이 없으므로 nullable.
    audio_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Fish Audio 목소리 모델 ID. 합성 시 /v1/tts 의 reference_id 로 목소리를 지목한다.
    # 등록 요청 전/실패면 None. status=training 이면 모델은 있지만 아직 학습 중이다.
    speaker_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # status=failed 일 때의 사유. 앱이 재녹음 안내에 쓴다.
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    device: Mapped["Device"] = relationship(back_populates="voices")


class DndSetting(Base):
    """방해 금지 시간(Do Not Disturb). 기기당 1개."""

    __tablename__ = "dnd_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), unique=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    start_hour: Mapped[int] = mapped_column(Integer, default=23)
    end_hour: Mapped[int] = mapped_column(Integer, default=7)
    # 예외: 이 시간에도 긴급 알림은 보호자에게 보낸다.
    allow_urgent_alert: Mapped[bool] = mapped_column(Boolean, default=True)
    # 예외: "모리야" 호출에는 응답한다.
    allow_wake_word: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    device: Mapped["Device"] = relationship(back_populates="dnd")


class Medication(Base):
    """약 복용 시간. 설정한 시각에 인형이 알려준다."""

    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    time: Mapped[str] = mapped_column(String(5))  # "HH:MM" (기기 로컬 시각)
    timing: Mapped[str] = mapped_column(String(10), default="식후")  # 식전|식후|공복|아무때나
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    device: Mapped["Device"] = relationship(back_populates="medications")


class InviteCode(Base):
    """가족 초대 코드. 가족 멤버 통계(생성 코드 수)에 쓰인다."""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("protectors.id", ondelete="SET NULL"), nullable=True
    )
    used_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("protectors.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="invite_codes")


# ── 기능 백엔드(remory-backend1)에서 합친 표들 ────────────────
# 홈·추억·대화·감정·활동·알림·리포트


class Memory(Base):
    """추억(사진 + 제목·시기·이야기). 나중에 임베딩 → Vector DB 로도 관리 예정."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    image_url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(100))
    period: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 예: "1980년대"
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmotionRecord(Base):
    """감정 기록 - 홈의 '현재 감정 / 감정 추이'용. (기록은 기기가 저장)"""

    __tablename__ = "emotion_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    emotion: Mapped[str] = mapped_column(String(20))  # happy | calm | sad ...
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActivityLog(Base):
    """활동 로그 - 홈의 '활동 타임라인'용. (기록은 기기가 저장)"""

    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(30))  # DAILY_CONVERSATION 등
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Utterance(Base):
    """인형과 어르신이 주고받은 말 한 줄. (기록은 기기가 저장)

    리포트를 만드는 재료로만 쓰고 오래 두지 않는다. 7일이 지난 것은
    데일리 배치가 지운다(scripts/generate_daily_reports.py). 앱에는
    내보내지 않는다 — 보호자가 읽는 것은 리포트 요약뿐이다.
    """

    __tablename__ = "utterances"
    # 하루치 조회와 7일 지난 것 삭제가 둘 다 (누구, 언제) 로 훑는다.
    __table_args__ = (Index("ix_utterances_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    speaker: Mapped[str] = mapped_column(String(10))  # user(어르신) | mori(인형)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SafetyEvent(Base):
    """인형이 대화에서 가려낸 위험 신호. (기록은 기기가 저장)

    kind: self_harm(자해) | harm_others(타해) | medical(의료 판단)
        | abuse(학대 정황) | profanity(거친 말)

    발췌는 그때 하신 말의 일부다. 민감한 내용이라 발화(utterances)와 같은
    기간만 두고 데일리 배치가 함께 지운다. 리포트 문구는 남으므로 무슨 일이
    있었는지는 나중에도 알 수 있다.
    """

    __tablename__ = "safety_events"
    __table_args__ = (Index("ix_safety_events_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """알림 - 홈 상단 배지 및 알림 센터용."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[int] = mapped_column(Integer, default=0)  # 0=긴급, 1=리포트 ...
    title: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FamilyChatMessage(Base):
    """가족 대화방 메시지. 인형 화면에 표시되고 음성으로 읽어준다."""

    __tablename__ = "family_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    sender_type: Mapped[str] = mapped_column(String(10))  # user | protector | system
    sender_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("protectors.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 어르신 답장의 목소리 녹음. 글(content)은 받아 적은 것이고 이쪽이 원본이다.
    audio_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    delivered_to_device: Mapped[bool] = mapped_column(Boolean, default=False)
    displayed_on_device: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatReadState(Base):
    """보호자가 그 대화방을 어디까지 읽었는지.

    메시지마다 '누가 읽었다' 를 남기지 않고, 사람마다 마지막으로 읽은 자리
    하나만 둔다. 메시지가 쌓여도 가족 수만큼만 줄이 생긴다. 메시지 id 는
    시간순으로 늘어나므로 last_read_message_id 보다 작거나 같으면 읽은 것이다.
    """

    __tablename__ = "chat_read_states"
    __table_args__ = (
        UniqueConstraint("user_id", "protector_id", name="uq_chat_read_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    protector_id: Mapped[int] = mapped_column(
        ForeignKey("protectors.id", ondelete="CASCADE"), index=True
    )
    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DailyReport(Base):
    """데일리 리포트 - 하루 대화·감정·활동 요약.

    scripts/generate_daily_reports.py 가 하루에 한 번 만든다.
    """

    __tablename__ = "daily_reports"
    # 같은 날 리포트가 두 건 생기지 않게 막는다. 배치를 다시 돌려도 안전하다.
    __table_args__ = (UniqueConstraint("user_id", "report_date", name="uq_daily_report_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 어느 날의 요약인지(한국 시간 기준). 예전 행에는 없을 수 있어 nullable.
    report_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    conversation_count: Mapped[int] = mapped_column(Integer, default=0)
    family_interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    emotion_summary: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 그날 나눈 이야기에서 몇 대목. JSON 배열이며 한 항목이 한 번의 주고받기다
    # ({"at","user","mori"}). 발화(utterances)는 7일 뒤 지워지므로 리포트를
    # 만들 때 뽑아 여기 둔다 — 그러지 않으면 지난 리포트가 영영 빈 채로 남는다.
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 하루가 어떻게 흘렀는지 풀어 쓴 글. 위의 summary 는 화면 맨 위에 크게
    # 걸리는 한 줄 머리말이고, 이쪽은 그 아래에서 하루를 이어서 들려준다.
    day_story: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 보호자가 오늘 해볼 만한 것. 모델이 쓰며, 못 만들면 비어 있다.
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WeeklyReport(Base):
    """주간 리포트 - 일주일 데일리 데이터 종합.

    scripts/generate_weekly_reports.py 가 주에 한 번 만든다.
    """

    __tablename__ = "weekly_reports"
    # 같은 주 리포트가 두 건 생기지 않게 막는다. 배치를 다시 돌려도 안전하다.
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_weekly_report_week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 어느 주의 요약인지 — 그 주 월요일(한국 시간). 예전 행에는 없어 nullable.
    week_start: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    total_conversation_count: Mapped[int] = mapped_column(Integer, default=0)
    family_interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_emotion_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0~100
    dominant_emotion: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    emergency_alert_count: Mapped[int] = mapped_column(Integer, default=0)
    weekly_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 한 주가 어떻게 흘렀는지 풀어 쓴 글. weekly_summary 는 맨 위에 걸리는
    # 머리말이고, 이쪽은 아래에서 한 주를 돌아보며 들려준다.
    week_story: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 그 주에 자주 나온 이야깃거리. JSON 배열 [{"word","count"}].
    # 횟수는 모델이 세지 않는다 — 모델은 몇 번째 대화에 나왔는지만 짚고,
    # 세는 일은 배치가 한다. 지어낸 숫자를 '12번' 이라고 보여줄 수는 없다.
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 요일별 감정. JSON 배열 [{"date","weekday","emotion","score"}].
    # 그날 기록이 없으면 emotion 이 null 이다.
    daily_emotions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
