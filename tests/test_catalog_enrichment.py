from app.services.catalog_enrichment import (
    aggregate_business_hours,
    build_delivery_text,
    format_listing_type,
    haversine_miles,
    is_open_now,
)


def test_format_listing_type():
    assert format_listing_type("subscription") == "Subscription"
    assert format_listing_type("one_time") == "One-time"
    assert format_listing_type(None) == "One-time"


def test_build_delivery_text():
    assert build_delivery_text("per week", "free delivery") == "per week · free delivery"
    assert build_delivery_text(None, "free delivery") == "free delivery"
    assert build_delivery_text("per week", None) == "per week"
    assert build_delivery_text(None, None) is None


def test_haversine_miles_same_point():
    assert haversine_miles(40.7128, -74.0060, 40.7128, -74.0060) == 0.0


def test_is_open_now_respects_closed_day():
    hours = [
        {"day": "Monday", "open": "09:00", "close": "17:00", "is_closed": True},
    ]
    assert is_open_now(hours) is False


def test_aggregate_business_hours_from_service_schedule():
    class _Service:
        availability_schedule = [
            {
                "day": "monday",
                "is_available": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "slot_length": "60",
            }
        ]

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return [_Service()]

    class _Db:
        def query(self, _model):
            return _Query()

    hours = aggregate_business_hours(_Db(), "00000000-0000-0000-0000-000000000001")
    monday = next(row for row in hours if row["day"] == "Monday")
    assert monday["open"] == "09:00"
    assert monday["close"] == "17:00"
    assert monday["is_closed"] is False
