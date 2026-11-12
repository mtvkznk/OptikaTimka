from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models import CatalogCategory, Product

DEFAULT_CATEGORIES = {
    "frames": "Оправи",
    "sunglasses": "Сонцезахисні",
    "contact-lenses": "Контактні лінзи",
    "kids-frames": "Дитячі оправи",
}


def category_records(session: Session) -> dict[str, CatalogCategory | None]:
    rows = {row.slug: row for row in session.exec(select(CatalogCategory))}
    categories = {slug: rows.pop(slug, None) for slug in DEFAULT_CATEGORIES}
    categories.update(
        (row.slug, row) for row in sorted(rows.values(), key=lambda row: (row.name.casefold(), row.slug))
    )
    return categories


def category_names(session: Session) -> dict[str, str]:
    return {
        slug: row.name if row is not None else DEFAULT_CATEGORIES[slug]
        for slug, row in category_records(session).items()
        if row is None or not row.is_removed
    }


def product_category():
    return func.lower(func.replace(Product.category, "_", "-"))


def active_products() -> SelectOfScalar[Product]:
    return select(Product).where(Product.is_active).order_by(Product.sort_order, Product.id)


def public_products() -> SelectOfScalar[Product]:
    return active_products().where(visible_category())


def visible_category():
    removed = select(CatalogCategory.slug).where(CatalogCategory.is_removed)
    return product_category().not_in(removed)
