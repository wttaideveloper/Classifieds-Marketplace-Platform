"""
Event Management Phase 2.7 — the pure accommodation rules (no database, no HTTP): app/utils/event_accommodation.py
and the accommodation schemas.

The same shape as Phase 2.6 meals (tests/test_event_meals_config.py), minus the ``date`` field an accommodation
option does not have. Covers: tolerant reading of stored config, stable option ids (kept, reused by name, never
regenerated), retiring instead of deleting, duplicate / empty / malformed rejection, the enabled-flag rules, and
selection validation (unknown, retired, duplicates, order, legacy events).

Run:
    pytest tests/test_event_accommodation_config.py -v
"""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.event_accommodation_schema import (
    AccommodationOptionInput,
    EventAccommodationInput,
    EventAccommodationSelectionUpdate,
)
from app.utils.event_accommodation import (
    ACCOMMODATION_ID_PATTERN,
    MAX_INPUT_OPTIONS,
    MAX_STORED_OPTIONS,
    AccommodationConfigError,
    AccommodationSelectionError,
    accommodation_enabled,
    accommodation_for_new_event,
    accommodation_selection_views,
    build_accommodation_options,
    plan_accommodation_update,
    resolve_event_accommodation,
    stored_accommodation_options,
    validate_accommodation_selections,
)

ON = {"registration": True, "tickets": False, "sessions": False, "check_in": True, "online_meeting": False,
      "custom_questions": False, "meals": False, "accommodation": True}
OFF = {**ON, "accommodation": False}


def opt(option_id, name, active=True, description=None):
    return {"id": option_id, "name": name, "description": description, "active": active}


# Phase 2.8: see the identical helpers in test_event_meals_config.py for why read vs write need different
# expected currency values.
_PRICED_DEFAULTS_READ = {"price": "0.00", "currency": "INR", "capacity": None,
                         "purchase_start_at": None, "purchase_end_at": None, "service_start_at": None, "service_end_at": None}
_PRICED_DEFAULTS_WRITE = {**_PRICED_DEFAULTS_READ, "currency": None}


def event(options=None, modules=ON, accommodation="unset"):
    stored = {"options": options} if accommodation == "unset" and options is not None else (None if accommodation == "unset" else accommodation)
    return SimpleNamespace(modules=modules, accommodation=stored, pricing_type="free", price=None, ticket_types=[], sessions=[],
                           delivery_mode="in_person", meeting_link=None, meeting_provider=None, custom_fields=[],
                           custom_values=[], form_configuration_version_id=None)


TWO = [opt("shared-room", "Shared Room"), opt("private-room", "Private Room")]


# ===========================================================================
# reading stored config (never raises, never invents)
# ===========================================================================


class TestReading:
    def test_a_legacy_event_has_no_accommodation_and_it_is_off(self):
        legacy = SimpleNamespace(modules=None, accommodation=None, pricing_type="free", price=None, ticket_types=[], sessions=[],
                                 delivery_mode="in_person", meeting_link=None, meeting_provider=None, custom_fields=[],
                                 custom_values=[], form_configuration_version_id=None)
        assert accommodation_enabled(legacy) is False
        assert stored_accommodation_options(legacy) == []
        assert resolve_event_accommodation(legacy) == {"enabled": False, "options": []}

    def test_enabled_is_modules_accommodation_and_nothing_else(self):
        assert resolve_event_accommodation(event(TWO, ON))["enabled"] is True
        assert resolve_event_accommodation(event(TWO, OFF))["enabled"] is False  # options are kept, the switch is modules.accommodation
        assert stored_accommodation_options(event(TWO, OFF)) == [{**o, **_PRICED_DEFAULTS_READ} for o in TWO]

    def test_options_are_returned_normalised(self):
        stored = event([{"id": "a", "name": "  Alpha  ", "description": "  ", "active": True}])
        assert stored_accommodation_options(stored) == [{**opt("a", "Alpha"), **_PRICED_DEFAULTS_READ}]

    @pytest.mark.parametrize("garbage", [
        "text", 7, [], {"options": "text"}, {"options": {"a": 1}}, {"options": [None, 1, "x", []]},
        {"options": [{"id": "a"}]}, {"options": [{"name": "No id"}]}, {"options": [{"id": "", "name": "Empty id"}]},
        {"options": [{"id": "bad id", "name": "Spaces"}]}, {"options": [{"id": "a", "name": "   "}]},
        {"options": [{"id": 5, "name": "Numeric id"}]}, {"options": [{"id": "a", "name": 5}]},
    ])
    def test_malformed_stored_data_is_skipped_never_raised(self, garbage):
        assert stored_accommodation_options(event(accommodation=garbage)) == []
        assert resolve_event_accommodation(event(accommodation=garbage))["options"] == []

    def test_only_an_explicit_false_retires(self):
        stored = event([{"id": "a", "name": "A", "active": False}, {"id": "b", "name": "B"}, {"id": "c", "name": "C", "active": None}])
        assert [o["active"] for o in stored_accommodation_options(stored)] == [False, True, True]

    def test_duplicate_stored_ids_keep_the_first(self):
        stored = event([opt("Shared", "First"), opt("shared", "Second")])
        assert [o["name"] for o in stored_accommodation_options(stored)] == ["First"]


# ===========================================================================
# building options: ids, retiring, validation
# ===========================================================================


class TestBuildOptions:
    def test_new_options_without_ids_get_unique_generated_ids(self):
        built = build_accommodation_options([{"name": "Shared Room"}, {"name": "Private Room"}], [])
        assert len({o["id"] for o in built}) == 2 and all(ACCOMMODATION_ID_PATTERN.match(o["id"]) for o in built)
        assert [o["active"] for o in built] == [True, True]

    def test_client_supplied_ids_are_kept(self):
        assert [o["id"] for o in build_accommodation_options([{"id": "shared-room", "name": "Shared Room"}], [])] == ["shared-room"]

    def test_a_known_id_keeps_its_identity_when_renamed_or_reordered(self):
        existing = TWO
        built = build_accommodation_options(
            [{"id": "private-room", "name": "Deluxe Room"}, {"id": "shared-room", "name": "Shared Room"}], existing)
        assert [(o["id"], o["name"]) for o in built] == [("private-room", "Deluxe Room"), ("shared-room", "Shared Room")]

    def test_an_option_without_an_id_reuses_the_existing_one_with_the_same_name(self):
        existing = [opt("r1", "Shared Room"), opt("r2", "Private Room")]
        built = build_accommodation_options([{"name": "shared room"}, {"name": "PRIVATE ROOM "}], existing)
        assert [o["id"] for o in built] == ["r1", "r2"]  # name compared case-insensitively

    def test_repeated_updates_never_change_ids(self):
        first = build_accommodation_options([{"name": "A"}, {"name": "B"}], [])
        second = build_accommodation_options([{"name": "A"}, {"name": "B"}], first)
        third = build_accommodation_options([{"name": "B"}, {"name": "A"}], second)
        assert [o["id"] for o in second] == [o["id"] for o in first]
        assert sorted(o["id"] for o in third) == sorted(o["id"] for o in first)

    def test_options_an_update_stops_listing_are_retired_not_deleted(self):
        built = build_accommodation_options([{"id": "shared-room", "name": "Shared Room"}], TWO)
        assert [(o["id"], o["active"]) for o in built] == [("shared-room", True), ("private-room", False)]
        assert built[1]["name"] == "Private Room"  # everything else about it survives

    def test_an_empty_list_retires_everything(self):
        assert [o["active"] for o in build_accommodation_options([], TWO)] == [False, False]

    def test_relisting_a_retired_option_reactivates_it_with_its_id(self):
        retired = [opt("shared-room", "Shared Room", active=False)]
        assert build_accommodation_options([{"name": "Shared Room"}], retired) == [{**opt("shared-room", "Shared Room"), **_PRICED_DEFAULTS_WRITE}]
        assert build_accommodation_options([{"id": "shared-room", "name": "Shared Room"}], retired) == [{**opt("shared-room", "Shared Room"), **_PRICED_DEFAULTS_WRITE}]

    def test_an_explicit_active_false_retires_a_listed_option(self):
        assert build_accommodation_options([{"id": "shared-room", "name": "Shared Room", "active": False}], [])[0]["active"] is False

    def test_duplicate_ids_are_rejected_case_insensitively(self):
        with pytest.raises(AccommodationConfigError, match="Duplicate accommodation option id"):
            build_accommodation_options([{"id": "Shared", "name": "A"}, {"id": "shared", "name": "B"}], [])

    def test_duplicate_active_options_are_rejected(self):
        with pytest.raises(AccommodationConfigError, match="Duplicate accommodation option: 'shared room'"):  # names the repeated (second) entry
            build_accommodation_options([{"name": "Shared Room"}, {"name": " shared room "}], [])

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_an_empty_name_is_rejected(self, name):
        with pytest.raises(AccommodationConfigError, match="non-empty name"):
            build_accommodation_options([{"name": name}], [])

    @pytest.mark.parametrize("bad_id", ["", " ", "has space", "ünï", "a/b", "-leading", "_leading", "x" * 65, 5, ["a"]])
    def test_a_malformed_id_is_rejected(self, bad_id):
        with pytest.raises(AccommodationConfigError, match="ids may only contain"):
            build_accommodation_options([{"id": bad_id, "name": "Shared Room"}], [])

    def test_limits(self):
        with pytest.raises(AccommodationConfigError, match=f"At most {MAX_INPUT_OPTIONS}"):
            build_accommodation_options([{"name": f"m{i}"} for i in range(MAX_INPUT_OPTIONS + 1)], [])
        history = [opt(f"old{i}", f"Old {i}", active=False) for i in range(MAX_STORED_OPTIONS)]
        with pytest.raises(AccommodationConfigError, match=f"at most {MAX_STORED_OPTIONS}"):
            build_accommodation_options([{"name": "One more"}], history)


# ===========================================================================
# create + update planning against modules.accommodation
# ===========================================================================


class TestPlanning:
    def test_new_event_with_no_options_stores_nothing(self):
        assert accommodation_for_new_event(None, enabled=True) is None
        assert accommodation_for_new_event([], enabled=False) is None  # nothing to configure, nothing contradicted

    def test_new_event_options_need_accommodation_on(self):
        with pytest.raises(AccommodationConfigError, match="Accommodation is disabled"):
            accommodation_for_new_event([{"name": "Shared Room"}], enabled=False)
        stored = accommodation_for_new_event([{"name": "Shared Room"}], enabled=True)
        assert list(stored) == ["options"] and stored["options"][0]["name"] == "Shared Room"

    def test_update_with_no_options_is_no_change(self):
        assert plan_accommodation_update(event(TWO), None, enabled_after=True) is None
        assert plan_accommodation_update(event(TWO), None, enabled_after=False) is None

    def test_update_changes_are_planned_when_accommodation_is_on_after_the_update(self):
        planned = plan_accommodation_update(event(TWO), [{"id": "shared-room", "name": "Dorm"}], enabled_after=True)
        assert [(o["id"], o["name"], o["active"]) for o in planned["options"]] == [
            ("shared-room", "Dorm", True), ("private-room", "Private Room", False)]

    def test_changes_are_refused_while_accommodation_is_off(self):
        with pytest.raises(AccommodationConfigError, match="Accommodation is disabled"):
            plan_accommodation_update(event(TWO, OFF), [{"name": "Extra"}], enabled_after=False)

    def test_echoing_the_stored_options_back_is_a_no_op_even_while_off(self):
        echo = [dict(o) for o in TWO]
        assert plan_accommodation_update(event(TWO, OFF), echo, enabled_after=False) is None
        assert plan_accommodation_update(event(TWO, ON), echo, enabled_after=True) is None

    def test_a_legacy_event_echoing_no_options_is_a_no_op(self):
        legacy = event(None, modules=None)
        assert plan_accommodation_update(legacy, [], enabled_after=False) is None


# ===========================================================================
# attendee selections
# ===========================================================================


class TestSelections:
    def test_none_and_empty_mean_no_selection_and_never_need_accommodation_on(self):
        for selections in (None, []):
            assert validate_accommodation_selections(event(TWO, ON), selections) is None
            assert validate_accommodation_selections(event(TWO, OFF), selections) is None
            assert validate_accommodation_selections(event(None, modules=None), selections) is None

    def test_valid_selections_are_returned_in_the_events_option_order(self):
        assert validate_accommodation_selections(event(TWO), ["private-room", "shared-room"]) == ["shared-room", "private-room"]

    def test_accommodation_must_be_on(self):
        with pytest.raises(AccommodationSelectionError, match="not enabled"):
            validate_accommodation_selections(event(TWO, OFF), ["shared-room"])
        with pytest.raises(AccommodationSelectionError, match="not enabled"):
            validate_accommodation_selections(event(None, modules=None), ["shared-room"])  # legacy: no accommodation

    def test_unknown_ids_are_rejected_and_named(self):
        with pytest.raises(AccommodationSelectionError, match="Unknown accommodation option.*: cabin, tent"):
            validate_accommodation_selections(event(TWO), ["shared-room", "tent", "cabin"])

    def test_the_configuration_is_the_source_of_truth(self):
        stored = event([opt("shared-room", "Shared Room")])
        with pytest.raises(AccommodationSelectionError, match="Unknown"):
            validate_accommodation_selections(stored, ["private-room"])  # refused when no private room is configured

    def test_ids_are_matched_exactly(self):
        with pytest.raises(AccommodationSelectionError, match="Unknown"):
            validate_accommodation_selections(event(TWO), ["Shared-Room"])

    def test_duplicates_are_rejected(self):
        with pytest.raises(AccommodationSelectionError, match="Duplicate accommodation selection: shared-room"):
            validate_accommodation_selections(event(TWO), ["shared-room", "shared-room"])

    @pytest.mark.parametrize("bad", [[None], [1], [["shared-room"]], [{"id": "shared-room"}], ["shared-room", None]])
    def test_non_string_items_are_rejected(self, bad):
        with pytest.raises(AccommodationSelectionError, match="list of accommodation option ids"):
            validate_accommodation_selections(event(TWO), bad)

    def test_retired_options_cannot_be_newly_selected_but_may_be_kept(self):
        stored = event([opt("shared-room", "Shared Room"), opt("private-room", "Private Room", active=False)])
        with pytest.raises(AccommodationSelectionError, match="no longer available: Private Room"):
            validate_accommodation_selections(stored, ["private-room"])
        assert validate_accommodation_selections(stored, ["shared-room", "private-room"], current=["private-room"]) == ["shared-room", "private-room"]
        with pytest.raises(AccommodationSelectionError, match="no longer available"):
            validate_accommodation_selections(stored, ["private-room"], current=["shared-room"])

    def test_views_carry_names_flags_and_option_order(self):
        options = stored_accommodation_options(event([opt("shared-room", "Shared Room"), opt("private-room", "Private Room", active=False)]))
        assert accommodation_selection_views(options, ["private-room", "shared-room"]) == [
            {"accommodation_id": "shared-room", "name": "Shared Room", "active": True},
            {"accommodation_id": "private-room", "name": "Private Room", "active": False},
        ]

    def test_views_show_unknown_ids_rather_than_hide_them(self):
        options = stored_accommodation_options(event(TWO))
        assert accommodation_selection_views(options, ["shared-room", "ghost"]) == [
            {"accommodation_id": "shared-room", "name": "Shared Room", "active": True},
            {"accommodation_id": "ghost", "name": None, "active": False},
        ]

    @pytest.mark.parametrize("junk", [None, "shared-room", 7, {"shared-room": True}, [None, 3]])
    def test_views_never_raise_on_odd_stored_data(self, junk):
        assert accommodation_selection_views(stored_accommodation_options(event(TWO)), junk) == []


# ===========================================================================
# schemas
# ===========================================================================


class TestSchemas:
    def test_option_input_validation(self):
        assert AccommodationOptionInput(name="  Shared Room ").name == "Shared Room"
        assert AccommodationOptionInput(name="Shared Room", description="Bunk beds").as_dict()["description"] == "Bunk beds"
        for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "A", "id": ""}, {"name": "A", "id": "bad id"},
                    {"name": "A", "id": "x" * 65}, {"name": "A", "active": "yes"},
                    {"name": "A", "description": "x" * 301}, {"name": "A", "price": -5}, {}, {"id": "a"}):
            with pytest.raises(ValidationError):
                AccommodationOptionInput(**bad)

    def test_accommodation_input_rules(self):
        assert EventAccommodationInput().options is None  # nothing sent = no change
        assert EventAccommodationInput(enabled=True, options=[{"name": "A"}]).option_dicts()[0]["name"] == "A"
        for bad in ({"options": "Shared Room"}, {"options": [None]}, {"options": ["Shared Room"]}, {"options": {"name": "A"}},
                    {"enabled": "yes"}, {"enabled": 1}, {"vendor": "x"},
                    {"options": [{"name": f"m{i}"} for i in range(MAX_INPUT_OPTIONS + 1)]}):
            with pytest.raises(ValidationError):
                EventAccommodationInput(**bad)

    def test_selection_update_rules(self):
        assert EventAccommodationSelectionUpdate(accommodation_selections=[]).accommodation_selections == []
        assert EventAccommodationSelectionUpdate(accommodation_selections=["shared-room", "uuid-like-0f8a"]).accommodation_selections
        for bad in ({}, {"accommodation_selections": None}, {"accommodation_selections": "shared-room"}, {"accommodation_selections": [None]},
                    {"accommodation_selections": [1]}, {"accommodation_selections": ["shared-room", "shared-room"]},
                    {"accommodation_selections": ["bad id"]}, {"accommodation_selections": [""]}, {"accommodation_selections": ["x"] * 101},
                    {"accommodation_selections": ["a"], "tenant_id": "x"}, {"accommodation_selections": {"shared-room": True}}):
            with pytest.raises(ValidationError):
                EventAccommodationSelectionUpdate(**bad)
