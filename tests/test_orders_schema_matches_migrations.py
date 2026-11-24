"""Regression for the checkout 500: the Order model had declared
`refund_reason` since the refund flow was added, but no migration ever
created that column on the `orders` table (only on the unrelated
`event_orders` table) — every checkout INSERT failed in Postgres with
"column refund_reason of relation orders does not exist".

This statically cross-checks every column the `Order` model declares against
every column any alembic migration actually adds to the `orders` table, so a
model column added without a matching migration fails CI instead of 500ing
on the next checkout.
"""

import re
from pathlib import Path

from app.models.cart_model import Order

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# op.create_table("orders", sa.Column("foo", ...), ...) or
# op.add_column("orders", sa.Column("foo", ...))
_CREATE_TABLE_RE = re.compile(r"op\.create_table\(\s*['\"]orders['\"]", re.MULTILINE)
_ADD_COLUMN_RE = re.compile(r"op\.add_column\(\s*['\"]orders['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"]", re.MULTILINE)
_COLUMN_IN_BLOCK_RE = re.compile(r"sa\.Column\(\s*['\"](\w+)['\"]")


def _columns_added_to_orders_by_migrations() -> set[str]:
    columns: set[str] = set()
    for path in _VERSIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")

        for match in _ADD_COLUMN_RE.finditer(text):
            columns.add(match.group(1))

        for create_match in _CREATE_TABLE_RE.finditer(text):
            # Grab the create_table(...) call body: from the match up to the
            # next unmatched ')' at the same nesting level is overkill here —
            # migrations in this repo call create_table with one argument per
            # line, so slicing to the next "op.create_index"/"def " is enough.
            start = create_match.end()
            end = text.find("\n)\n", start)
            if end == -1:
                end = start + 3000
            block = text[start:end]
            for col_match in _COLUMN_IN_BLOCK_RE.finditer(block):
                columns.add(col_match.group(1))

    return columns


def test_every_order_model_column_has_a_migration():
    model_columns = set(Order.__table__.columns.keys())
    migrated_columns = _columns_added_to_orders_by_migrations()

    missing = model_columns - migrated_columns
    assert not missing, (
        f"Order model declares column(s) {sorted(missing)} with no matching "
        f"op.create_table/op.add_column('orders', ...) in any alembic migration — "
        f"this is exactly the bug that caused checkout to 500. Add a migration."
    )


def test_every_order_status_fits_the_status_columns():
    """Regression for the cancel 500 on PostgreSQL: "cancellation_requested" (22 characters) did not fit VARCHAR(20), and SQLite (the tests) does not enforce
    a length, so only a check against the declared column sizes can catch it."""
    from app.models.cart_model import OrderStatusEvent
    from app.services.order_service import ADMIN_TRANSITIONS, TERMINAL

    statuses = {"pending", "cancellation_requested", *TERMINAL, *ADMIN_TRANSITIONS, *(s for targets in ADMIN_TRANSITIONS.values() for s in targets)}
    limits = {"orders.status": Order.__table__.c.status, "orders.status_before_request": Order.__table__.c.status_before_request,
              "order_status_events.from_status": OrderStatusEvent.__table__.c.from_status, "order_status_events.to_status": OrderStatusEvent.__table__.c.to_status}
    for name, column in limits.items():
        too_long = sorted(s for s in statuses if len(s) > column.type.length)
        assert not too_long, f"{name} is VARCHAR({column.type.length}) but these statuses are longer: {too_long}"
    for column in (Order.__table__.c.cancelled_by_role, OrderStatusEvent.__table__.c.actor_role):
        assert column.type.length >= len("super_admin")


def test_a_migration_widens_the_status_columns_to_match_the_model():
    text = (_VERSIONS_DIR / "s8d1f4b7c0e5_widen_order_status_columns.py").read_text(encoding="utf-8")
    assert "sa.String(32)" in text
    for table, column in (("orders", "status"), ("orders", "status_before_request"), ("order_status_events", "from_status"), ("order_status_events", "to_status")):
        assert f'("{table}", "{column}"' in text
