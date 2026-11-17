"""추억(사진 + 제목·시기·이야기) 등록·조회·삭제."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_protector
from ..errors import APIError, envelope
from ..models import Memory, Protector
from ..schemas import MemoryCreateRequest
from ..services.access import ensure_own_image, get_owned_user, memory_json

router = APIRouter(tags=["memories"])


@router.post("/users/{user_id}/memories", status_code=201)
def create_memory(
    user_id: int,
    body: MemoryCreateRequest,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """추억 등록. 사진은 POST /files/images 로 먼저 올리고 그 URL을 보낸다."""
    user = get_owned_user(db, protector, user_id)
    ensure_own_image(body.image_url, protector.id)
    ensure_own_image(body.original_image_url, protector.id)

    memory = Memory(
        user_id=user.id,
        image_url=body.image_url,
        original_image_url=body.original_image_url,
        title=body.title.strip(),
        period=body.period,
        description=body.description,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)

    # TODO(RAG 담당): 여기서 임베딩 → Vector DB 저장 연동 예정
    return envelope(memory_json(memory), "추억을 등록했습니다.", 201)


@router.get("/users/{user_id}/memories")
def list_memories(
    user_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """추억 목록 조회 (최신순)."""
    user = get_owned_user(db, protector, user_id)
    memories = db.scalars(
        select(Memory).where(Memory.user_id == user.id).order_by(Memory.created_at.desc())
    ).all()
    return envelope([memory_json(m) for m in memories], "OK", 200)


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    protector: Protector = Depends(get_current_protector),
):
    """추억 삭제."""
    memory = db.get(Memory, memory_id)
    if memory is None:
        raise APIError(404, "추억을 찾을 수 없습니다.")
    get_owned_user(db, protector, memory.user_id)  # 권한 없으면 404

    db.delete(memory)
    db.commit()
    # TODO(RAG 담당): Vector DB 임베딩도 함께 삭제 연동 예정
    return envelope({"memoryId": memory_id}, "추억을 삭제했습니다.", 200)
