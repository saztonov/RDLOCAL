"""Общие утилиты GUI"""

from datetime import datetime, timedelta, timezone


def format_datetime_utc3(dt_str: str) -> str:
    """Конвертировать UTC время в UTC+3 (МСК)"""
    try:
        if dt_str.endswith("Z"):
            dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        elif "+" not in dt_str and "T" in dt_str:
            dt_utc = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
        else:
            dt_utc = datetime.fromisoformat(dt_str)

        utc3 = timezone(timedelta(hours=3))
        dt_local = dt_utc.astimezone(utc3)

        return dt_local.strftime("%H:%M %d.%m.%Y")
    except Exception:
        return dt_str
