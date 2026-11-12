from typing import Literal

from sqlalchemy import func
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
    overrides = category_overrides(session)
    return {
        slug: overrides[slug].name if slug in overrides else name
        for slug, name in DEFAULT_CATEGORIES.items()
        if slug not in overrides or not overrides[slug].is_removed
    }


def product_category():
    return func.lower(func.replace(Product.category, "_", "-"))


def active_products() -> SelectOfScalar[Product]:
    return select(Product).where(Product.is_active).order_by(Product.sort_order, Product.id)


def public_products() -> SelectOfScalar[Product]:
    removed = select(CatalogCategory.slug).where(CatalogCategory.is_removed)
    return active_products().where(product_category().not_in(removed))
