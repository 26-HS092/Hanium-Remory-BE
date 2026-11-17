"""업로드 사진을 화면에 띄우기 좋은 크기로 줄인다.

원본을 그대로 두면(최대 수 MB) 인형이 미국 S3 에서 받느라 사진이 1~5초 늦게
뜬다. 앱도 줄여서 올리지만, 예전 앱이나 웹에서 올린 큰 사진은 여기서 막는다.
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from ..errors import APIError

# 인형 화면(800×480)의 두 배. 더 큰 화면이 붙어도 버틸 여유.
MAX_SIDE = 1600
JPEG_QUALITY = 80

# 10MB 업로드 제한 안에서도 압축 폭탄(작은 파일, 거대한 픽셀)은 들어올 수 있다.
Image.MAX_IMAGE_PIXELS = 50_000_000


def to_display_jpeg(content: bytes) -> bytes:
    """EXIF 회전을 실제 회전으로 반영하고, 긴 변 1600px 이하 JPEG 로 만든다.

    작은 사진은 키우지 않는다. 투명한 부분은 흰 바탕으로 채운다.
    """
    try:
        img = Image.open(BytesIO(content))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise APIError(400, "이미지 파일을 읽을 수 없습니다.")

    img = ImageOps.exif_transpose(img)
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel("A"))
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    out = BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
