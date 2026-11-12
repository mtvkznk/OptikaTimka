from typing import Literal

from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models import CatalogCategory, Product

CategorySlug = Literal["frames", "sunglasses", "contact-lenses", "kids-frames"]
DEFAULT_CATEGORIES: dict[CategorySlug, str] = {
    "frames": "Оправи",
    "sunglasses": "Сонцезахисні",
    "contact-lenses": "Контактні лінзи",
    "kids-frames": "Дитячі оправи",
}


def category_overrides(session: Session) -> dict[str, CatalogCategory]:
    return {
        row.slug: row for row in session.exec(select(CatalogCategory))
        if row.slug in DEFAULT_CATEGORIES
    }


def category_names(session: Session) -> dict[str, str]:
    names: dict[str, str] = dict(DEFAULT_CATEGORIES)
    names.update((slug, row.name) for slug, row in category_overrides(session).items())
    return names


def active_products() -> SelectOfScalar[Product]:
    return select(Product).where(Product.is_active).order_by(Product.sort_order, Product.id)
