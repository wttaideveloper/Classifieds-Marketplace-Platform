"""CSV downloads for the finance reports."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import Response


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_cell(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "; ".join(_cell(v) for v in value)
    text = str(value)
    # a cell that starts like a formula would be run by a spreadsheet; keep it text
    return "'" + text if text[:1] in ("=", "+", "-", "@") and not _is_number(text) else text


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def csv_response(rows: list[dict], columns: list[str], filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(column)) for column in columns])
    return Response(content=buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
