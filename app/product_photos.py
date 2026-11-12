from base64 import b64decode
from io import BytesIO

from fastapi import HTTPException
from PIL import Image
from sqlmodel import Session

from app.models import Product, ProductPhoto

MAX_PHOTO_PAYLOAD = 192 * 1024
MAX_PHOTO_DIMENSION = 1200


def product_photo_url(product_id: int) -> str:
    return f"/catalog/photos/{product_id}.jpg"


def prepare_photo(payload: str) -> bytes:
    try:
        raw = b64decode(payload, validate=True)
        with Image.open(BytesIO(raw), formats=["JPEG"]) as image:
            if not (1 <= min(image.size) <= max(image.size) <= MAX_PHOTO_DIMENSION):
                raise ValueError("Invalid dimensions")
            with BytesIO() as output:
                # Write a new JPEG so uploaded metadata is not retained.
                image.convert("RGB").save(output, "JPEG", quality=83, optimize=True)
                return output.getvalue()
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Фото пошкоджене або завелике. Виберіть інше зображення.") from exc


def update_product_photo(session: Session, product: Product, jpeg: bytes | None) -> None:
    if product.id is None:
        session.flush()
    photo = session.get(ProductPhoto, product.id)
    if jpeg is None:
        if photo is not None:
            session.delete(photo)
        product.image_url = None
        return
    if photo is None:
        session.add(ProductPhoto(product_id=product.id, jpeg=jpeg))
    else:
        photo.jpeg = jpeg
    product.image_url = product_photo_url(product.id)
