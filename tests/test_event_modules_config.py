"""
Event Management Phase 2.2 — pure unit tests for the event type / module configuration layer.

No database, no HTTP: app/utils/event_modules.py is pure by design (no writes, so nothing can be
"backfilled" from a read path). The default matrix below is written out independently from the product
spec, so the code cannot drift from it unnoticed.

Run:
    pytest tests/test_event_modules_config.py -v
"""
from types import SimpleNamespace
from typing import get_args
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.schemas.common_schema import EventType
from app.schemas.event_schema import EventCreate, EventModules, EventModulesInput, EventUpdate
from app.utils.event_modules import (
    DEFAULT_EVENT_TYPE,
    EVENT_MODULE_KEYS,
    EVENT_TYPE_DEFAULT_MODULES,
    EVENT_TYPES,
    EventModuleConfigError,
    clean_overrides,
    default_modules_for,
    is_paid_event,
    legacy_modules,
    modules_for_new_event,
    plan_config_update,
    resolve_event_modules,
    resolve_event_type,
    validate_module_overrides,
)

# ---- the product spec, typed out independently of the implementation --------------------------------
SPEC_TYPES = ["conference", "workshop", "marathon", "camp", "private_function", "webinar", "other"]
SPEC_KEYS = ["registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions", "meals", "accommodation"]


def flags(*enabled):
    return {key: key in enabled for key in SPEC_KEYS}


SPEC_DEFAULTS = {
    "conference": flags("registration", "tickets", "sessions", "check_in", "meals", "accommodation"),
    "workshop": flags("registration", "tickets", "sessions", "check_in"),
    "marathon": flags("registration", "tickets", "check_in"),
    "camp": flags("registration", "check_in", "meals", "accommodation"),
    "private_function": flags("registration", "check_in", "custom_questions", "meals"),
    "webinar": flags("registration", "sessions", "online_meeting"),
    "other": flags("registration"),
}


def event(**attrs):
    """A minimal event-like object (defaults = an ordinary legacy free in-person event)."""
    base = dict(
        event_type=None, modules=None, pricing_type="free", price=None, ticket_types=[], sessions=[],
        delivery_mode="in_person", meeting_link=None, meeting_provider=None, custom_fields=[],
        custom_values=[], form_configuration_version_id=None,
    )
    base.update(attrs)
    return SimpleNamespace(**base)


# ===========================================================================
# vocabulary + defaults
# ===========================================================================


class TestVocabularyAndDefaults:
    def test_supported_event_types_are_exactly_the_spec(self):
        assert list(EVENT_TYPES) == SPEC_TYPES
        assert list(get_args(EventType)) == SPEC_TYPES  # the schema Literal and the defaults table cannot diverge

    def test_supported_modules_are_exactly_the_spec(self):
        assert list(EVENT_MODULE_KEYS) == SPEC_KEYS

    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_default_modules_match_the_spec_for_every_type(self, event_type):
        assert EVENT_TYPE_DEFAULT_MODULES[event_type] == SPEC_DEFAULTS[event_type]
        assert default_modules_for(event_type) == SPEC_DEFAULTS[event_type]

    def test_every_default_lists_all_eight_modules_with_boolean_values(self):
        for event_type, modules in EVENT_TYPE_DEFAULT_MODULES.items():
            assert list(modules) == SPEC_KEYS, event_type
            assert all(type(value) is bool for value in modules.values()), event_type

    def test_defaults_are_copies_so_callers_cannot_corrupt_the_table(self):
        copy = default_modules_for("conference")
        copy["meals"] = False
        assert EVENT_TYPE_DEFAULT_MODULES["conference"]["meals"] is True
        assert default_modules_for("conference")["meals"] is True

    def test_unknown_or_missing_type_falls_back_to_the_safest_default(self):
        assert DEFAULT_EVENT_TYPE == "other"
        assert default_modules_for(None) == SPEC_DEFAULTS["other"]
        assert default_modules_for("party") == SPEC_DEFAULTS["other"]


# ===========================================================================
# legacy resolution (read side)
# ===========================================================================


class TestLegacyResolution:
    def test_legacy_type_is_other(self):
        assert resolve_event_type(event()) == "other"
        assert resolve_event_type(event(event_type="")) == "other"

    def test_unexpected_stored_type_never_breaks_a_read(self):
        assert resolve_event_type(event(event_type="party")) == "other"
        assert resolve_event_type(event(event_type=7)) == "other"
        assert resolve_event_type(MagicMock()) == "other"

    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_stored_type_is_returned_as_is(self, event_type):
        assert resolve_event_type(event(event_type=event_type)) == event_type

    def test_plain_legacy_event_keeps_registration_and_check_in_only(self):
        assert legacy_modules(event()) == flags("registration", "check_in")

    @pytest.mark.parametrize("attrs,module", [
        ({"pricing_type": "paid"}, "tickets"),
        ({"price": "250"}, "tickets"),
        ({"pricing_type": "free", "ticket_types": [{"id": "t", "name": "GA", "price": "10"}]}, "tickets"),
        ({"sessions": [{"id": "s", "title": "Keynote"}]}, "sessions"),
        ({"delivery_mode": "online"}, "online_meeting"),
        ({"delivery_mode": "hybrid"}, "online_meeting"),
        ({"delivery_mode": "in_person", "meeting_link": "https://meet.example.com/x"}, "online_meeting"),
        ({"meeting_provider": "zoom"}, "online_meeting"),
        ({"custom_fields": [{"label": "Diet", "type": "select", "options": ["veg"]}]}, "custom_questions"),
        ({"custom_values": [{"field_id": "f", "value": "v"}]}, "custom_questions"),
        ({"form_configuration_version_id": "3d0a3f5e-0000-4000-8000-000000000001"}, "custom_questions"),
    ])
    def test_a_capability_the_event_already_uses_stays_enabled(self, attrs, module):
        """The point of the fallback: nothing an existing event relies on is switched off."""
        assert legacy_modules(event(**attrs))[module] is True

    def test_free_priced_zero_tickets_are_not_ticketing(self):
        assert legacy_modules(event(price="0", ticket_types=[{"price": "0"}]))["tickets"] is False

    def test_meals_and_accommodation_are_never_inferred(self):
        full = event(pricing_type="paid", sessions=[{"id": "s"}], delivery_mode="hybrid", custom_fields=[{"label": "x"}])
        resolved = legacy_modules(full)
        assert resolved["meals"] is False and resolved["accommodation"] is False
        assert [k for k, v in resolved.items() if v] == ["registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions"]

    def test_legacy_resolution_follows_the_event_as_it_changes(self):
        ev = event()
        assert legacy_modules(ev)["sessions"] is False
        ev.sessions = [{"id": "s"}]
        assert legacy_modules(ev)["sessions"] is True  # computed, never stored

    def test_stored_modules_win_even_when_they_contradict_behaviour(self):
        stored = flags("registration")  # sessions off although the event has sessions
        assert resolve_event_modules(event(modules=stored, sessions=[{"id": "s"}], pricing_type="paid")) == stored

    def test_partial_or_corrupt_stored_modules_are_completed_per_key_not_guessed(self):
        ev = event(modules={"meals": True, "tickets": "yes", "sessions": 1}, sessions=[{"id": "s"}], pricing_type="paid")
        resolved = resolve_event_modules(ev)
        assert list(resolved) == SPEC_KEYS and all(type(v) is bool for v in resolved.values())
        assert resolved["meals"] is True  # the valid stored value is kept
        assert resolved["tickets"] is True and resolved["sessions"] is True  # non-booleans fall back to behaviour

    @pytest.mark.parametrize("stored", [None, {}, [], "junk", 5])
    def test_empty_or_wrong_shaped_stored_modules_resolve_as_legacy(self, stored):
        assert resolve_event_modules(event(modules=stored)) == flags("registration", "check_in")

    def test_mock_objects_do_not_break_resolution(self):
        resolved = resolve_event_modules(MagicMock())
        assert list(resolved) == SPEC_KEYS and all(type(v) is bool for v in resolved.values())


# ===========================================================================
# create planning
# ===========================================================================


class TestModulesForNewEvent:
    def test_no_type_and_no_overrides_stays_legacy(self):
        assert modules_for_new_event(None, None, event()) is None
        assert modules_for_new_event(None, {}, event()) is None

    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_type_only_applies_that_types_defaults(self, event_type):
        assert modules_for_new_event(event_type, None, event()) == SPEC_DEFAULTS[event_type]

    def test_empty_overrides_are_the_same_as_none(self):
        assert modules_for_new_event("camp", {}, event()) == SPEC_DEFAULTS["camp"]

    def test_explicit_override_wins_over_the_default(self):
        conference = modules_for_new_event("conference", {"meals": False}, event())
        assert conference == {**SPEC_DEFAULTS["conference"], "meals": False}
        webinar = modules_for_new_event("webinar", {"custom_questions": True}, event())
        assert webinar == {**SPEC_DEFAULTS["webinar"], "custom_questions": True}

    def test_a_full_explicit_configuration_is_used_as_given(self):
        given = flags("registration", "meals")
        assert modules_for_new_event("conference", dict(given), event()) == given

    def test_overrides_without_a_type_sit_on_top_of_the_behaviour_based_modules(self):
        ev = event(delivery_mode="online", sessions=[{"id": "s"}])
        assert modules_for_new_event(None, {"meals": True}, ev) == {
            **flags("registration", "check_in", "sessions", "online_meeting"), "meals": True}

    def test_a_type_default_never_switches_ticketing_off_for_a_paid_event(self):
        paid = event(pricing_type="paid", price="100")
        assert modules_for_new_event("camp", None, paid)["tickets"] is True
        assert modules_for_new_event("private_function", None, paid)["tickets"] is True
        assert modules_for_new_event("camp", None, event())["tickets"] is False  # free stays per the default

    def test_other_defaults_are_untouched_by_the_paid_safeguard(self):
        paid = event(ticket_types=[{"price": "5"}])
        assert modules_for_new_event("camp", None, paid) == {**SPEC_DEFAULTS["camp"], "tickets": True}

    def test_the_result_is_a_fresh_dict(self):
        result = modules_for_new_event("conference", None, event())
        result["meals"] = False
        assert SPEC_DEFAULTS["conference"]["meals"] is True and EVENT_TYPE_DEFAULT_MODULES["conference"]["meals"] is True


# ===========================================================================
# update planning
# ===========================================================================


def plan(ev, new_type=None, overrides=None, delivery_mode="in_person", is_paid=False):
    return plan_config_update(ev, new_type, overrides, delivery_mode=delivery_mode, is_paid=is_paid)


class TestPlanConfigUpdate:
    def test_an_update_that_does_not_mention_configuration_changes_nothing(self):
        for ev in (event(), event(event_type="conference", modules=SPEC_DEFAULTS["conference"])):
            assert plan(ev) == {}

    def test_overrides_merge_over_the_persisted_configuration(self):
        ev = event(event_type="conference", modules={**SPEC_DEFAULTS["conference"], "meals": False})
        assert plan(ev, overrides={"accommodation": False}) == {
            "modules": {**SPEC_DEFAULTS["conference"], "meals": False, "accommodation": False}}

    def test_changing_the_type_never_rewrites_persisted_modules(self):
        custom = {**SPEC_DEFAULTS["conference"], "meals": False}
        ev = event(event_type="conference", modules=custom)
        assert plan(ev, new_type="workshop") == {"event_type": "workshop"}
        assert plan(ev, new_type="camp") == {"event_type": "camp"}

    def test_type_and_overrides_together_keep_the_rest_of_the_persisted_config(self):
        custom = {**SPEC_DEFAULTS["conference"], "meals": False}
        ev = event(event_type="conference", modules=custom)
        assert plan(ev, new_type="workshop", overrides={"custom_questions": True}) == {
            "event_type": "workshop", "modules": {**custom, "custom_questions": True}}

    def test_a_never_configured_event_gets_the_new_types_defaults_when_the_type_changes(self):
        assert plan(event(), new_type="camp") == {"event_type": "camp", "modules": SPEC_DEFAULTS["camp"]}

    def test_echoing_back_the_resolved_other_does_not_reset_a_legacy_event(self):
        """A client that reads a legacy event (type resolved to 'other') and sends it back must not lose modules."""
        ev = event(pricing_type="paid", sessions=[{"id": "s"}], delivery_mode="online")
        assert plan(ev, new_type="other") == {"event_type": "other"}  # modules stay NULL -> still behaviour-based

    def test_setting_the_same_type_again_is_a_no_op(self):
        ev = event(event_type="camp", modules=SPEC_DEFAULTS["camp"])
        assert plan(ev, new_type="camp") == {}

    def test_first_time_configuration_with_a_type_and_overrides_starts_from_that_types_defaults(self):
        assert plan(event(), new_type="workshop", overrides={"meals": True}) == {
            "event_type": "workshop", "modules": {**SPEC_DEFAULTS["workshop"], "meals": True}}

    def test_first_time_configuration_with_only_overrides_starts_from_the_behaviour_based_modules(self):
        ev = event(sessions=[{"id": "s"}])
        assert plan(ev, overrides={"meals": True}) == {"modules": {**flags("registration", "check_in", "sessions"), "meals": True}}

    def test_a_type_change_on_a_never_configured_paid_event_keeps_ticketing(self):
        changes = plan(event(pricing_type="paid"), new_type="camp", is_paid=True)
        assert changes["modules"]["tickets"] is True

    def test_planning_is_deterministic(self):
        ev = event()
        assert plan(ev, new_type="webinar", overrides={"meals": True}, delivery_mode="online") == plan(
            ev, new_type="webinar", overrides={"meals": True}, delivery_mode="online")

    def test_planning_never_mutates_the_event_or_its_modules(self):
        stored = dict(SPEC_DEFAULTS["conference"])
        ev = event(event_type="conference", modules=stored)
        plan(ev, new_type="camp", overrides={"meals": False})
        assert ev.modules is stored and stored == SPEC_DEFAULTS["conference"] and ev.event_type == "conference"

    def test_contradictions_are_rejected_before_anything_is_planned(self):
        with pytest.raises(EventModuleConfigError):
            plan(event(), overrides={"online_meeting": True}, delivery_mode="in_person")
        with pytest.raises(EventModuleConfigError):
            plan(event(), overrides={"tickets": False}, is_paid=True)


# ===========================================================================
# validation rules
# ===========================================================================


class TestOverrideValidation:
    @pytest.mark.parametrize("mode", ["online", "hybrid", "ONLINE"])
    def test_online_meeting_is_allowed_for_online_and_hybrid(self, mode):
        validate_module_overrides({"online_meeting": True}, delivery_mode=mode, is_paid=False)

    @pytest.mark.parametrize("mode", ["in_person", "", None, "something"])
    def test_online_meeting_needs_an_online_format(self, mode):
        with pytest.raises(EventModuleConfigError, match="online_meeting"):
            validate_module_overrides({"online_meeting": True}, delivery_mode=mode, is_paid=False)

    def test_turning_online_meeting_off_is_always_fine(self):
        validate_module_overrides({"online_meeting": False}, delivery_mode="in_person", is_paid=False)

    def test_tickets_cannot_be_disabled_for_a_paid_event(self):
        with pytest.raises(EventModuleConfigError, match="tickets"):
            validate_module_overrides({"tickets": False}, delivery_mode="in_person", is_paid=True)
        validate_module_overrides({"tickets": False}, delivery_mode="in_person", is_paid=False)
        validate_module_overrides({"tickets": True}, delivery_mode="in_person", is_paid=True)

    def test_disabling_sessions_is_allowed_and_never_a_data_rule(self):
        validate_module_overrides({"sessions": False}, delivery_mode="in_person", is_paid=False)

    def test_is_paid_event_agrees_with_how_checkout_decides(self):
        assert is_paid_event("paid", None, []) and is_paid_event("free", "10", []) and is_paid_event("free", None, [{"price": "1"}])
        assert not is_paid_event("free", None, []) and not is_paid_event(None, "0", [{"price": "0"}])
        assert is_paid_event("free", None, [SimpleNamespace(price="9")])  # pydantic ticket objects too

    def test_clean_overrides_keeps_only_real_boolean_module_settings(self):
        assert clean_overrides({"meals": True, "polls": True, "tickets": None, "sessions": "yes"}) == {"meals": True}
        assert clean_overrides(None) == {}


# ===========================================================================
# Pydantic input schema
# ===========================================================================


class TestModulesInputSchema:
    def test_partial_overrides_are_accepted_and_report_only_what_was_sent(self):
        parsed = EventModulesInput.model_validate({"meals": False, "online_meeting": True})
        assert parsed.overrides() == {"meals": False, "online_meeting": True}

    def test_all_eight_modules_are_accepted(self):
        parsed = EventModulesInput.model_validate({key: True for key in SPEC_KEYS})
        assert list(parsed.overrides()) == SPEC_KEYS

    @pytest.mark.parametrize("bad", [{"polls": True}, {"networking": True}, {"Meals": True}, {"meals": True, "sponsors": False}])
    def test_unknown_module_names_are_rejected(self, bad):
        with pytest.raises(ValidationError, match="Extra inputs"):
            EventModulesInput.model_validate(bad)

    @pytest.mark.parametrize("module", SPEC_KEYS)
    @pytest.mark.parametrize("value", ["true", "yes", 1, 0, 1.0, [], {}, "false"])
    def test_non_boolean_values_are_rejected_for_every_module(self, module, value):
        with pytest.raises(ValidationError):
            EventModulesInput.model_validate({module: value})

    @pytest.mark.parametrize("module", SPEC_KEYS)
    def test_explicit_null_is_rejected_for_every_module(self, module):
        with pytest.raises(ValidationError, match="not null"):
            EventModulesInput.model_validate({module: None})

    @pytest.mark.parametrize("module", SPEC_KEYS)
    def test_real_booleans_are_accepted_for_every_module(self, module):
        assert EventModulesInput.model_validate({module: True}).overrides() == {module: True}
        assert EventModulesInput.model_validate({module: False}).overrides() == {module: False}

    @pytest.mark.parametrize("bad", ["meals", ["meals"], 5, True])
    def test_a_non_object_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            EventModulesInput.model_validate(bad)

    def test_the_response_model_requires_all_eight_booleans(self):
        assert list(EventModules.model_fields) == SPEC_KEYS
        with pytest.raises(ValidationError):
            EventModules.model_validate({"registration": True})
        assert EventModules.model_validate(SPEC_DEFAULTS["camp"]).model_dump() == SPEC_DEFAULTS["camp"]


class TestCreateAndUpdateSchemas:
    BASE = {"title": "T", "category": "c", "start_date": "2030-01-01T10:00:00", "end_date": "2030-01-01T12:00:00"}

    def test_both_fields_are_optional_so_existing_clients_are_unaffected(self):
        create = EventCreate.model_validate(self.BASE)
        assert create.event_type is None and create.modules is None
        data = create.to_model_data()
        assert data["event_type"] is None and data["modules"] is None  # stays legacy: nothing stored
        update = EventUpdate.model_validate({"title": "x"})
        assert update.event_type is None and update.modules is None

    @pytest.mark.parametrize("event_type", SPEC_TYPES)
    def test_create_model_data_carries_the_type_and_its_defaults(self, event_type):
        data = EventCreate.model_validate({**self.BASE, "event_type": event_type}).to_model_data()
        assert data["event_type"] == event_type and data["modules"] == SPEC_DEFAULTS[event_type]

    def test_create_model_data_applies_explicit_overrides(self):
        data = EventCreate.model_validate({**self.BASE, "event_type": "conference", "modules": {"meals": False}}).to_model_data()
        assert data["modules"] == {**SPEC_DEFAULTS["conference"], "meals": False}

    def test_a_paid_create_keeps_ticketing_even_for_a_ticketless_type(self):
        data = EventCreate.model_validate({**self.BASE, "event_type": "camp", "pricing_type": "paid", "price": "50"}).to_model_data()
        assert data["modules"]["tickets"] is True

    @pytest.mark.parametrize("event_type", ["party", "Conference", "CONFERENCE", "", 3, "private-function"])
    def test_invalid_event_types_are_rejected_on_create_and_update(self, event_type):
        with pytest.raises(ValidationError):
            EventCreate.model_validate({**self.BASE, "event_type": event_type})
        with pytest.raises(ValidationError):
            EventUpdate.model_validate({"event_type": event_type})

    def test_update_model_data_never_carries_configuration(self):
        """Configuration has partial-update semantics that need the current event, so the service plans it."""
        data = EventUpdate.model_validate({"title": "x", "event_type": "camp", "modules": {"meals": True}}).to_model_data()
        assert data == {"title": "x"}

    def test_stored_configuration_still_validates_as_an_event_create(self):
        """validate_event_submission re-validates stored columns through EventCreate; stored config must pass."""
        stored = {**self.BASE, "event_type": "webinar", "modules": SPEC_DEFAULTS["webinar"], "delivery_mode": "in_person"}
        EventCreate.model_validate(stored)  # a default-derived config is shape-valid whatever the delivery mode is
