"""추억을 앱에서 고치고 지울 수 있는지.

같은 어르신의 가족이면 누가 올린 추억이든 고칠 수 있고, 다른 집 추억은
건드릴 수 없어야 한다.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import FamilyMember, Memory, Protector, User
from app.security import create_access_token

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


def _bearer(pid):
    return {"Authorization": f"Bearer {create_access_token(pid)}"}


@pytest.fixture
def family():
    """어르신 한 분에 가족 둘(올린 사람·초대받은 사람), 남의 집 보호자 하나."""
    db = TestSession()
    try:
        owner = Protector(phone_number="01011112222", display_name="김지영", user_handle=b"h1")
        invited = Protector(phone_number="01033334444", display_name="김민수", user_handle=b"h2")
        stranger = Protector(phone_number="01055556666", display_name="이남", user_handle=b"h3")
        db.add_all([owner, invited, stranger])
        db.flush()
        user = User(name="박순자")
        other = User(name="최영희")
        db.add_all([user, other])
        db.flush()
        db.add_all([
            FamilyMember(user_id=user.id, protector_id=owner.id, is_primary=True),
            FamilyMember(user_id=user.id, protector_id=invited.id, is_primary=False),
            FamilyMember(user_id=other.id, protector_id=stranger.id, is_primary=True),
        ])
        memory = Memory(
            user_id=user.id,
            image_url="https://example.com/crop.jpg",
            original_image_url="https://example.com/full.jpg",
            title="테스트 추억",
            period="2020년",
            description="예전 테스트",
        )
        db.add(memory)
        db.commit()
        return {
            "owner": owner.id, "invited": invited.id, "stranger": stranger.id,
            "user": user.id, "memory": memory.id,
        }
    finally:
        db.close()


def test_patch_changes_only_sent_fields(family):
    r = client.patch(
        f"/memories/{family['memory']}",
        headers=_bearer(family["owner"]),
        json={"title": "  제주도 여행  ", "description": "1985년 신혼여행"},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["title"] == "제주도 여행"
    assert d["description"] == "1985년 신혼여행"
    assert d["period"] == "2020년"                      # 안 보낸 건 그대로
    assert d["imageUrl"] == "https://example.com/crop.jpg"


def test_empty_period_clears_it(family):
    r = client.patch(
        f"/memories/{family['memory']}",
        headers=_bearer(family["owner"]),
        json={"period": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["period"] is None


def test_blank_title_is_rejected(family):
    r = client.patch(
        f"/memories/{family['memory']}",
        headers=_bearer(family["owner"]),
        json={"title": ""},
    )
    assert r.status_code in (400, 422)


def test_new_photo_without_original_drops_old_original(family):
    r = client.patch(
        f"/memories/{family['memory']}",
        headers=_bearer(family["owner"]),
        json={"imageUrl": "https://example.com/new.jpg"},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["imageUrl"] == "https://example.com/new.jpg"
    # 예전 원본이 남으면 인형이 바뀌기 전 사진을 읽는다.
    assert d["originalImageUrl"] == "https://example.com/new.jpg"


def test_invited_family_can_edit_and_delete(family):
    h = _bearer(family["invited"])
    r = client.patch(f"/memories/{family['memory']}", headers=h, json={"title": "할머님 추억"})
    assert r.status_code == 200, r.text

    r = client.delete(f"/memories/{family['memory']}", headers=h)
    assert r.status_code == 200, r.text
    r = client.get(f"/users/{family['user']}/memories", headers=h)
    assert r.json()["data"] == []


def test_stranger_cannot_touch(family):
    h = _bearer(family["stranger"])
    r = client.patch(f"/memories/{family['memory']}", headers=h, json={"title": "x"})
    assert r.status_code == 404
    r = client.delete(f"/memories/{family['memory']}", headers=h)
    assert r.status_code == 404


def test_missing_memory_is_404(family):
    r = client.patch("/memories/9999", headers=_bearer(family["owner"]), json={"title": "x"})
    assert r.status_code == 404
