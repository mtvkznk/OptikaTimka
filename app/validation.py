import re


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower() or None


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    raw = re.sub(r"[\s().-]", "", value.strip())
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", raw):
        return None
    return "+" + raw.lstrip("+")
