"""푸시 토큰 등록·해제와, 알림이 보호자 설정을 지키는지.

발송 자체(FCM REST)는 서비스 계정이 없으면 꺼지므로 여기서는 타지 않는다.
확인하려는 것은 '누구에게 알림을 만들고 누구의 토큰을 고르는가' 다.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    FamilyMember,
    SafetyEvent,
    Notification,
    NotificationSetting,
    Protector,
    PushToken,
    User,
)
from app.security import create_access_token
from app.services import notifications as notif

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


client = TestClient(app)


@pytest.fixture
def world():
    """보호자 둘(김지영·김민수) + 어르신(박순자)."""
    db = TestSession()
    try:
        me = Protector(
            phone_number="01011112222", display_name="김지영", user_handle=b"h1"
        )
        other = Protector(
            phone_number="01033334444", display_name="김민수", user_handle=b"h2"
        )
        db.add_all([me, other])
        db.flush()
        user = User(name="박순자", gender="female", birth_date=dt.date(1952, 3, 15))
        db.add(user)
        db.flush()
        db.add_all(
            [
                FamilyMember(user_id=user.id, protector_id=me.id, is_primary=True),
                FamilyMember(user_id=user.id, protector_id=other.id),
            ]
        )
        db.commit()
        return {"me": me.id, "other": other.id, "user": user.id}
    finally:
        db.close()


def auth(protector_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(protector_id)}"}


def data(response):
    assert response.status_code < 400, response.text
    return response.json()["data"]


# ── 토큰 등록 ────────────────────────────────────────
def test_register_saves_the_token(world):
    result = data(
        client.post(
            "/protectors/me/push-tokens",
            json={"token": "fcm-abc", "platform": "android"},
            headers=auth(world["me"]),
        )
    )
    assert result["protectorId"] == world["me"]

    db = TestSession()
    try:
        rows = db.scalars(select(PushToken)).all()
        assert len(rows) == 1
        assert rows[0].token == "fcm-abc"
    finally:
        db.close()


def test_registering_twice_does_not_duplicate(world):
    for _ in range(2):
        client.post(
            "/protectors/me/push-tokens",
            json={"token": "fcm-abc"},
            headers=auth(world["me"]),
        )
    db = TestSession()
    try:
        assert len(db.scalars(select(PushToken)).all()) == 1
    finally:
        db.close()


def test_same_phone_relogged_as_someone_else_changes_owner(world):
    """폰을 물려주면 같은 토큰이 새 주인 것이 되어야 한다."""
    client.post(
        "/protectors/me/push-tokens", json={"token": "fcm-abc"}, headers=auth(world["me"])
    )
    client.post(
        "/protectors/me/push-tokens",
        json={"token": "fcm-abc"},
        headers=auth(world["other"]),
    )
    db = TestSession()
    try:
        rows = db.scalars(select(PushToken)).all()
        assert len(rows) == 1
        assert rows[0].protector_id == world["other"]
    finally:
        db.close()


def test_unknown_platform_is_rejected(world):
    response = client.post(
        "/protectors/me/push-tokens",
        json={"token": "fcm-abc", "platform": "windows"},
        headers=auth(world["me"]),
    )
    assert response.status_code == 422


def test_push_tokens_need_login(world):
    response = client.post("/protectors/me/push-tokens", json={"token": "fcm-abc"})
    assert response.status_code == 401


def test_unregister_removes_only_my_token(world):
    client.post(
        "/protectors/me/push-tokens", json={"token": "mine"}, headers=auth(world["me"])
    )
    client.post(
        "/protectors/me/push-tokens", json={"token": "theirs"}, headers=auth(world["other"])
    )

    # 남의 토큰을 지우려 해도 남아 있어야 한다.
    client.request(
        "DELETE",
        "/protectors/me/push-tokens",
        json={"token": "theirs"},
        headers=auth(world["me"]),
    )
    db = TestSession()
    try:
        assert sorted(t.token for t in db.scalars(select(PushToken)).all()) == [
            "mine",
            "theirs",
        ]
    finally:
        db.close()

    client.request(
        "DELETE",
        "/protectors/me/push-tokens",
        json={"token": "mine"},
        headers=auth(world["me"]),
    )
    db = TestSession()
    try:
        assert [t.token for t in db.scalars(select(PushToken)).all()] == ["theirs"]
    finally:
        db.close()


# ── 알림 설정을 지키는지 ─────────────────────────────
def test_notification_goes_to_everyone_by_default(world):
    db = TestSession()
    try:
        made = notif.notify_report_ready(db, world["user"], "요약")
        assert made == 2
        assert len(db.scalars(select(Notification)).all()) == 2
    finally:
        db.close()


def test_a_protector_who_turned_it_off_gets_nothing(world):
    db = TestSession()
    try:
        db.add(NotificationSetting(protector_id=world["other"], daily_report=False))
        db.commit()

        made = notif.notify_report_ready(db, world["user"], "요약")
        assert made == 1
        rows = db.scalars(select(Notification)).all()
        assert [n.protector_id for n in rows] == [world["me"]]
    finally:
        db.close()


def test_turning_off_the_whole_urgent_group_wins(world):
    """'긴급' 을 끄면 세부 항목이 켜져 있어도 오지 않는다."""
    db = TestSession()
    try:
        db.add(
            NotificationSetting(
                protector_id=world["other"], urgent=False, emotion_change=True
            )
        )
        db.commit()

        made = notif._create(
            db,
            user_id=world["user"],
            type_=notif.TYPE_URGENT,
            requires=("urgent", "emotion_change"),
            title=notif.EMOTION_TITLE,
            content="x",
        )
        assert made == 1
        rows = db.scalars(select(Notification)).all()
        assert [n.protector_id for n in rows] == [world["me"]]
    finally:
        db.close()


def test_settings_row_is_created_on_first_use(world):
    """설정을 한 번도 안 건드린 보호자도 기본값 줄이 생겨 다음부터 빨리 판단한다."""
    db = TestSession()
    try:
        notif.notify_report_ready(db, world["user"], "요약")
        assert len(db.scalars(select(NotificationSetting)).all()) == 2
    finally:
        db.close()


def test_push_is_skipped_when_not_configured(world):
    """서비스 계정이 없으면 푸시는 건너뛰고 알림만 만든다."""
    from app.services import fcm

    assert fcm.enabled() is False
    db = TestSession()
    try:
        db.add(PushToken(protector_id=world["me"], token="fcm-abc"))
        db.commit()
        assert notif.notify_report_ready(db, world["user"], "요약") == 2
    finally:
        db.close()


# ── 발송 경로 ────────────────────────────────────────
class _StubResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def fcm_ready(monkeypatch):
    """서비스 계정이 있는 것처럼 만들고, 나간 요청을 받아 둔다."""
    from app.services import fcm

    sent = []

    class _Creds:
        project_id = "remory-test"

    monkeypatch.setattr(fcm, "_load_credentials", lambda: _Creds())
    monkeypatch.setattr(fcm, "_access_token", lambda: "stub-access-token")

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "body": json})
        token = json["message"]["token"]
        # 'dead-' 로 시작하는 토큰은 FCM 이 404 UNREGISTERED 로 답한다고 본다.
        if token.startswith("dead-"):
            return _StubResponse(404, '{"error":{"status":"NOT_FOUND"}}')
        return _StubResponse(200)

    monkeypatch.setattr(fcm.httpx, "post", fake_post)
    return sent


def test_push_request_has_the_shape_fcm_expects(world, fcm_ready):
    db = TestSession()
    try:
        db.add(PushToken(protector_id=world["me"], token="live-1"))
        db.commit()
        notif.notify_report_ready(db, world["user"], "오늘은 평온하셨어요")
    finally:
        db.close()

    assert len(fcm_ready) == 1
    call = fcm_ready[0]
    assert call["url"] == (
        "https://fcm.googleapis.com/v1/projects/remory-test/messages:send"
    )
    assert call["headers"]["Authorization"] == "Bearer stub-access-token"

    message = call["body"]["message"]
    assert message["token"] == "live-1"
    assert message["notification"]["title"] == notif.REPORT_TITLE
    assert message["notification"]["body"] == "오늘은 평온하셨어요"
    # data 값은 FCM 이 문자열만 받는다.
    assert message["data"] == {"type": str(notif.TYPE_REPORT)}
    assert message["android"]["priority"] == "high"
    # 앱이 만든 '높음' 채널로 와야 폰 상단에 팝업으로 뜬다.
    assert message["android"]["notification"]["channel_id"] == "remory_alerts"


def test_dead_token_is_dropped(world, fcm_ready):
    db = TestSession()
    try:
        db.add_all(
            [
                PushToken(protector_id=world["me"], token="dead-1"),
                PushToken(protector_id=world["me"], token="live-1"),
            ]
        )
        db.commit()

        notif.notify_report_ready(db, world["user"], "요약")

        left = sorted(t.token for t in db.scalars(select(PushToken)).all())
        assert left == ["live-1"]
    finally:
        db.close()


def test_a_protector_who_turned_it_off_is_not_pushed(world, fcm_ready):
    db = TestSession()
    try:
        db.add(NotificationSetting(protector_id=world["other"], daily_report=False))
        db.add_all(
            [
                PushToken(protector_id=world["me"], token="live-me"),
                PushToken(protector_id=world["other"], token="live-other"),
            ]
        )
        db.commit()

        notif.notify_report_ready(db, world["user"], "요약")
    finally:
        db.close()

    assert [c["body"]["message"]["token"] for c in fcm_ready] == ["live-me"]


# ── 안전 신호 ────────────────────────────────────────
DEVICE_TOKEN = "safety-device-token"


@pytest.fixture
def paired(world):
    """world 에 인형을 하나 붙인다."""
    from app.models import Device

    db = TestSession()
    try:
        device = Device(user_id=world["user"], name="모리", device_token=DEVICE_TOKEN)
        db.add(device)
        db.commit()
        return {**world, "device": device.id}
    finally:
        db.close()


def device_auth() -> dict:
    return {"X-Device-Token": DEVICE_TOKEN}


def test_self_harm_is_recorded_and_alerts_family(paired, fcm_ready):
    db = TestSession()
    try:
        db.add(PushToken(protector_id=paired["me"], token="live-me"))
        db.commit()
    finally:
        db.close()

    result = data(
        client.post(
            f"/devices/{paired['device']}/safety-events",
            json={"kind": "self_harm", "excerpt": "이제 그만 죽고 싶어"},
            headers=device_auth(),
        )
    )
    assert result["alerted"] == 2          # 보호자 둘 다

    db = TestSession()
    try:
        events = db.scalars(select(SafetyEvent)).all()
        assert [e.kind for e in events] == ["self_harm"]
        titles = [n.title for n in db.scalars(select(Notification)).all()]
        assert titles == [notif.SELF_HARM_TITLE] * 2
    finally:
        db.close()

    assert [c["body"]["message"]["token"] for c in fcm_ready] == ["live-me"]


def test_abuse_is_recorded_but_not_alerted(paired, fcm_ready):
    """정황일 뿐이라 알리지 않는다. 알림이 의심받는 사람에게 그대로 가면
    어르신이 오히려 위험해질 수 있다."""
    result = data(
        client.post(
            f"/devices/{paired['device']}/safety-events",
            json={"kind": "abuse", "excerpt": "며느리가 때렸어"},
            headers=device_auth(),
        )
    )
    assert result["alerted"] == 0

    db = TestSession()
    try:
        assert len(db.scalars(select(SafetyEvent)).all()) == 1
        assert db.scalars(select(Notification)).all() == []
    finally:
        db.close()
    assert fcm_ready == []


def test_urgent_off_means_no_self_harm_alert(paired):
    """'긴급' 을 끈 보호자에게는 가지 않는다(다른 긴급 알림과 같은 기준)."""
    db = TestSession()
    try:
        db.add(NotificationSetting(protector_id=paired["other"], urgent=False))
        db.commit()
    finally:
        db.close()

    result = data(
        client.post(
            f"/devices/{paired['device']}/safety-events",
            json={"kind": "self_harm", "excerpt": "살기 싫어"},
            headers=device_auth(),
        )
    )
    assert result["alerted"] == 1


def test_watch_only_self_harm_is_recorded_but_not_alerted(paired, fcm_ready):
    """두 단계 알림. 인형이 '지켜볼 신호'(urgent: false)로 보낸 자해는 바로 알리지
    않는다 — LLM 혼자 본 위험은 오탐이 많다. 기록은 남아 그날 리포트에 담긴다."""
    db = TestSession()
    try:
        db.add(PushToken(protector_id=paired["me"], token="live-me"))
        db.commit()
    finally:
        db.close()

    result = data(
        client.post(
            f"/devices/{paired['device']}/safety-events",
            json={"kind": "self_harm", "excerpt": "다 귀찮다",
                  "urgent": False, "source": "llm"},
            headers=device_auth(),
        )
    )
    assert result["alerted"] == 0

    db = TestSession()
    try:
        event = db.scalars(select(SafetyEvent)).one()
        assert (event.kind, event.urgent, event.source) == ("self_harm", False, "llm")
        assert db.scalars(select(Notification)).all() == []
    finally:
        db.close()
    assert fcm_ready == []


def test_urgent_self_harm_from_device_alerts(paired):
    """인형이 긴급으로 보낸 자해는 지금처럼 바로 알린다."""
    result = data(
        client.post(
            f"/devices/{paired['device']}/safety-events",
            json={"kind": "self_harm", "excerpt": "살기 싫어",
                  "urgent": True, "source": "llm+model"},
            headers=device_auth(),
        )
    )
    assert result["alerted"] == 2

    db = TestSession()
    try:
        event = db.scalars(select(SafetyEvent)).one()
        assert (event.urgent, event.source) == (True, "llm+model")
    finally:
        db.close()


def test_old_device_without_urgent_still_alerts(paired):
    """urgent 를 보내지 않는 예전 인형은 지금까지처럼 긴급으로 본다."""
    result = data(
        client.post(
            f"/devices/{paired['device']}/safety-events",
            json={"kind": "self_harm", "excerpt": "살기 싫어"},
            headers=device_auth(),
        )
    )
    assert result["alerted"] == 2

    db = TestSession()
    try:
        assert db.scalars(select(SafetyEvent)).one().urgent is True
    finally:
        db.close()


def test_unknown_source_is_rejected(paired):
    response = client.post(
        f"/devices/{paired['device']}/safety-events",
        json={"kind": "self_harm", "excerpt": "x", "source": "gut"},
        headers=device_auth(),
    )
    assert response.status_code == 422


def test_unknown_kind_is_rejected(paired):
    response = client.post(
        f"/devices/{paired['device']}/safety-events",
        json={"kind": "swearing", "excerpt": "x"},
        headers=device_auth(),
    )
    assert response.status_code == 422


def test_safety_events_need_a_device_token(paired):
    response = client.post(
        f"/devices/{paired['device']}/safety-events",
        json={"kind": "self_harm", "excerpt": "x"},
    )
    assert response.status_code == 401


def test_an_unexpected_push_error_does_not_break_the_alert(world, monkeypatch):
    """발송 중 예상 못 한 예외가 나도 알림 생성(과 그걸 부른 요청)은 끝까지 간다."""
    from app.services import fcm

    monkeypatch.setattr(fcm, "enabled", lambda: True)

    def boom(*args, **kwargs):
        raise RuntimeError("네트워크가 이상하다")

    monkeypatch.setattr(fcm, "send", boom)
    db = TestSession()
    try:
        db.add(PushToken(protector_id=world["me"], token="live-1"))
        db.commit()
        assert notif.notify_report_ready(db, world["user"], "요약") == 2
        # 토큰이 죽은 게 아니므로 남아 있어야 한다.
        assert [t.token for t in db.scalars(select(PushToken)).all()] == ["live-1"]
    finally:
        db.close()


def test_a_bad_request_not_about_the_token_keeps_the_token(world, fcm_ready, monkeypatch):
    """400 이 토큰 탓이 아니면(메시지 모양이 틀린 경우 등) 토큰을 지우지 않는다.

    지워 버리면 서버 쪽 실수 한 번에 모든 가족의 폰이 알림을 못 받게 된다.
    """
    from app.services import fcm

    monkeypatch.setattr(
        fcm.httpx,
        "post",
        lambda *a, **k: _StubResponse(
            400,
            '{"error":{"code":400,"message":"Invalid JSON payload received.",'
            '"status":"INVALID_ARGUMENT"}}',
        ),
    )
    db = TestSession()
    try:
        db.add(PushToken(protector_id=world["me"], token="live-1"))
        db.commit()
        notif.notify_report_ready(db, world["user"], "요약")
        assert [t.token for t in db.scalars(select(PushToken)).all()] == ["live-1"]
    finally:
        db.close()


def test_an_invalid_token_is_dropped(world, fcm_ready, monkeypatch):
    """400 이라도 토큰이 잘못됐다고 하면 버린다."""
    from app.services import fcm

    monkeypatch.setattr(
        fcm.httpx,
        "post",
        lambda *a, **k: _StubResponse(
            400,
            '{"error":{"code":400,"message":"The registration token is not a valid '
            'FCM registration token","status":"INVALID_ARGUMENT"}}',
        ),
    )
    db = TestSession()
    try:
        db.add(PushToken(protector_id=world["me"], token="bad-1"))
        db.commit()
        notif.notify_report_ready(db, world["user"], "요약")
        assert db.scalars(select(PushToken)).all() == []
    finally:
        db.close()


def test_fcm_token_transport_is_installed():
    """FCM 액세스 토큰을 받는 데 쓰는 전송 계층이 설치돼 있어야 한다.

    google-auth 만 깔면 requests 가 빠져, 운영에서 모든 푸시가 ImportError 로
    조용히 실패한다(requirements.txt 의 google-auth[requests]).
    """
    import google.auth.transport.requests  # noqa: F401
