"""Training reports/dashboards endpoints — parity with Program's existing
/reports/summary, /{id}/dashboards/participant, /{id}/dashboards/provider,
/{id}/reports, which had no Training equivalent."""

from unittest.mock import MagicMock
from uuid import uuid4

from app.services import training_service


def test_participant_dashboard_reports_enrolment_status_and_progress(monkeypatch):
    training = MagicMock()
    enrol = MagicMock(status="enrolled")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_get_enrolment", lambda db, tid, email: enrol)
    monkeypatch.setattr(
        training_service,
        "get_training_progress_service",
        lambda db, tid, participant_email=None: {
            "overall_percent": 40, "sections_done": 1, "total_sections": 2,
            "lessons_done": 2, "total_lessons": 5, "certificate_url": None, "expired": False,
        },
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

    result = training_service.get_training_participant_dashboard_service(db, uuid4(), "learner@example.com")
    assert result["enrolment_status"] == "enrolled"
    assert result["overall_percent"] == 40


def test_participant_dashboard_not_enrolled_when_no_enrolment(monkeypatch):
    training = MagicMock()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_get_enrolment", lambda db, tid, email: None)
    monkeypatch.setattr(
        training_service,
        "get_training_progress_service",
        lambda db, tid, participant_email=None: {
            "overall_percent": 0, "sections_done": 0, "total_sections": 0,
            "lessons_done": 0, "total_lessons": 0, "certificate_url": None, "expired": False,
        },
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

    result = training_service.get_training_participant_dashboard_service(db, uuid4(), "learner@example.com")
    assert result["enrolment_status"] == "not_enrolled"


def test_provider_dashboard_computes_totals_and_capacity_utilization(monkeypatch):
    training = MagicMock(capacity="10")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()

    def query_side_effect(*args):
        q = MagicMock()
        q.filter.return_value.scalar.return_value = 5
        q.filter.return_value.group_by.return_value.all.return_value = [("enrolled", 3), ("completed", 2)]
        q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect

    result = training_service.get_training_provider_dashboard_service(db, uuid4())
    assert result["total_enrolments"] == 5
    assert result["capacity_utilization"] == 50.0
    assert result["by_status"] == {"enrolled": 3, "completed": 2}


def test_reports_enrolment_type_groups_by_status(monkeypatch):
    training = MagicMock(currency="INR")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    row1 = MagicMock(status="enrolled", created_at=None)
    row2 = MagicMock(status="enrolled", created_at=None)
    row3 = MagicMock(status="cancelled", created_at=None)

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [row1, row2, row3]

    result = training_service.get_training_reports_service(db, uuid4(), report_type="enrolment")
    assert result["data"]["total"] == 3
    assert result["data"]["by_status"] == {"enrolled": 2, "cancelled": 1}


def test_reports_revenue_type_sums_order_amounts(monkeypatch):
    training = MagicMock(currency="INR")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    order1 = MagicMock(amount="100", created_at=None)
    order2 = MagicMock(amount="50", created_at=None)

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [order1, order2]

    result = training_service.get_training_reports_service(db, uuid4(), report_type="revenue")
    assert result["data"]["total_revenue"] == "150.0"
    assert result["data"]["orders"] == 2


def test_summary_service_groups_by_status_and_category(monkeypatch):
    db = MagicMock()

    def query_side_effect(*args):
        q = MagicMock()
        q.filter.return_value.with_entities.return_value.group_by.return_value.all.return_value = [("published", 3)]
        q.filter.return_value.all.return_value = []
        q.filter.return_value.scalar.return_value = 7
        return q

    db.query.side_effect = query_side_effect

    result = training_service.get_training_summary_service(db)
    assert result["total_trainings"] == 3
    assert result["by_status"] == {"published": 3}
