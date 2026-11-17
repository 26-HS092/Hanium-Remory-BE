"""사진 업로드가 화면용 크기로 줄어드는지.

인형은 미국 S3 에서 사진을 받는다. 원본(수 MB)을 그대로 두면 화면에 1~5초
늦게 뜨므로, 서버가 긴 변 1600px JPEG 로 줄여 저장해야 한다.
"""

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import FamilyMember, Protector, User
from app.routers import files
from app.security import create_access_token
from app.services.storage import LocalStorage

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
def setup_db(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(files, "storage", LocalStorage(base_dir=str(tmp_path)))
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


client = TestClient(app)


@pytest.fixture
def auth(tmp_path):
    db = TestSession()
    try:
        me = Protector(phone_number="01011112222", display_name="김지영", user_handle=b"h-me")
        db.add(me)
        db.flush()
        user = User(name="박순자")
        db.add(user)
        db.flush()
        db.add(FamilyMember(user_id=user.id, protector_id=me.id, is_primary=True))
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(me.id)}"}, user.id
    finally:
        db.close()


def _jpeg(size, exif_orientation=None, mode="RGB"):
    img = Image.new(mode, size, (200, 100, 50) if mode == "RGB" else (200, 100, 50, 0))
    out = BytesIO()
    if mode == "RGB":
        exif = Image.Exif()
        if exif_orientation:
            exif[0x0112] = exif_orientation
        img.save(out, "JPEG", quality=95, exif=exif)
    else:
        img.save(out, "PNG")
    return out.getvalue()


def _stored(tmp_path, url):
    return Image.open(tmp_path / url[len("/uploads/"):])


def test_big_photo_is_shrunk_to_1600_jpeg(auth, tmp_path):
    headers, user_id = auth
    r = client.post(
        "/files/images",
        headers=headers,
        data={"userId": str(user_id)},
        files={"file": ("big.png", _jpeg((4000, 2400), mode="RGBA"), "image/png")},
    )
    assert r.status_code == 200, r.text
    url = r.json()["data"]["imageUrl"]
    assert url.endswith(".jpg")
    img = _stored(tmp_path, url)
    assert img.format == "JPEG"
    assert img.size == (1600, 960)
    assert "originalUrl" not in r.json()["data"]


def test_exif_rotation_is_applied(auth, tmp_path):
    headers, user_id = auth
    # 6 = 시계 방향 90도. 가로로 찍혀 저장됐지만 세로 사진이다.
    photo = _jpeg((2000, 1000), exif_orientation=6)
    r = client.post(
        "/files/images",
        headers=headers,
        data={"userId": str(user_id)},
        files={"file": ("p.jpg", photo, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    img = _stored(tmp_path, r.json()["data"]["imageUrl"])
    assert img.size == (800, 1600)
    assert img.getexif().get(0x0112) in (None, 1)


def test_small_photo_is_not_enlarged(auth, tmp_path):
    headers, user_id = auth
    r = client.post(
        "/files/images",
        headers=headers,
        data={"userId": str(user_id)},
        files={"file": ("s.jpg", _jpeg((500, 300)), "image/jpeg")},
    )
    assert _stored(tmp_path, r.json()["data"]["imageUrl"]).size == (500, 300)


def test_broken_image_is_rejected(auth):
    headers, user_id = auth
    r = client.post(
        "/files/images",
        headers=headers,
        data={"userId": str(user_id)},
        files={"file": ("x.jpg", b"not an image", "image/jpeg")},
    )
    assert r.status_code == 400
