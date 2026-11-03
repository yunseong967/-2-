"""날짜는 YYYY-MM-DD 한 형식만 허용한다. 다른 형식은 추측하지 않고 오류로 돌려보낸다."""
from __future__ import annotations

import re
from datetime import date

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date(value: str) -> date:
    if not _ISO.match(value or ""):
        raise ValueError(f"형식 오류(YYYY-MM-DD 아님): {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"존재하지 않는 날짜: {value!r}") from e
