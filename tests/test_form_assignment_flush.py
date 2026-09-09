"""Regression for a 500 reported when assigning a tenant to a new selective
Event Form Configuration that was previously assigned to a different,
inactive configuration.

Root cause: EventFormAssignment (and its Training/Program equivalents) has a
UNIQUE constraint on tenant_id alone. Reassigning that tenant deletes the old
row and inserts a new one for the same tenant_id in one flush. Without an
explicit flush between the delete and the insert, Postgres (and SQLite, as
used here) evaluates the unique constraint before the delete is actually
sent, raising IntegrityError -> an uncaught 500.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from app.schemas.event_form_config_schema import AssignmentPutRequest as EventAssignmentPutRequest
from app.schemas.training_form_config_schema import AssignmentPutRequest as TrainingAssignmentPutRequest
from app.schemas.program_form_config_schema import AssignmentPutRequest as ProgramAssignmentPutRequest
from app.services import event_form_config_service as event_svc
from app.services import training_form_config_service as training_svc
from app.services import program_form_config_service as program_svc


def _assert_flush_between_delete_and_add(module, assignment_model_name, config_model_name, payload_cls):
    config_id = uuid4()
    tenant_id = uuid4()
    enterprise_id = uuid4()

    config = MagicMock(scope="selective")
    monkeypatch_target = f"_get_config_or_404"
    original_get_config = getattr(module, monkeypatch_target)
    setattr(module, monkeypatch_target, lambda db, cid: config)

    enterprise = MagicMock(id=enterprise_id)
    original_resolve_enterprise = module._resolve_enterprise_for_tenant
    module._resolve_enterprise_for_tenant = lambda db, tid: enterprise

    other_config = MagicMock(is_active=False)
    conflict_row = MagicMock(configuration_id=uuid4(), tenant_id=tenant_id)

    db = MagicMock()
    calls: list[tuple[str, object]] = []
    db.delete.side_effect = lambda obj: calls.append(("delete", obj))
    db.flush.side_effect = lambda: calls.append(("flush", None))
    db.add.side_effect = lambda obj: calls.append(("add", obj))

    # Shared across every db.query(AssignmentModel)...first() call in this
    # test, in call order: the conflict lookup, then the existing_same lookup.
    first_results = iter([conflict_row, None])

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == assignment_model_name:
            q.filter.return_value.all.return_value = []
            q.filter.return_value.first.side_effect = lambda: next(first_results, None)
        elif model.__name__ == config_model_name:
            q.filter.return_value.first.return_value = other_config
        return q

    db.query.side_effect = query_side_effect

    try:
        payload = payload_cls(tenant_ids=[tenant_id])
        module.put_assignments_service(db, config_id, payload, {"id": "admin-1"})
    finally:
        setattr(module, monkeypatch_target, original_get_config)
        module._resolve_enterprise_for_tenant = original_resolve_enterprise

    kinds = [c[0] for c in calls]
    assert "delete" in kinds, f"expected delete of the conflicting row, got {kinds}"
    assert "add" in kinds, f"expected insert of the new assignment row, got {kinds}"

    delete_idx = kinds.index("delete")
    add_idx = kinds.index("add")
    assert any(k == "flush" for k in kinds[delete_idx:add_idx]), (
        f"delete of the conflicting row must be flushed before the reinsert for the same "
        f"tenant_id (unique constraint), got order={kinds}"
    )


def test_event_form_assignment_flushes_between_delete_and_insert():
    _assert_flush_between_delete_and_add(
        event_svc, "EventFormAssignment", "EventFormConfiguration", EventAssignmentPutRequest
    )


def test_training_form_assignment_flushes_between_delete_and_insert():
    _assert_flush_between_delete_and_add(
        training_svc, "TrainingFormAssignment", "TrainingFormConfiguration", TrainingAssignmentPutRequest
    )


def test_program_form_assignment_flushes_between_delete_and_insert():
    _assert_flush_between_delete_and_add(
        program_svc, "ProgramFormAssignment", "ProgramFormConfiguration", ProgramAssignmentPutRequest
    )
