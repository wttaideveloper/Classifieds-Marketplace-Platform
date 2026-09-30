"""
Event Management Phase 2.6 — the pure meal rules (no database, no HTTP): app/utils/event_meals.py and the meal schemas.

Covers: tolerant reading of stored config, stable option ids (kept, reused by name+date, never regenerated), retiring
instead of deleting, duplicate / empty / malformed rejection, the enabled-flag rules, and selection validation
(unknown, retired, duplicates, order, legacy events).

Run:
    pytest tests/test_event_meals_config.py -v
"""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.event_meal_schema import EventMealSelectionUpdate, EventMealsInput, MealOptionInput
from app.utils.event_meals import (
    MAX_INPUT_OPTIONS,
    MAX_STORED_OPTIONS,
    MEAL_ID_PATTERN,
    MealConfigError,
    MealSelectionError,
    build_options,
    meals_enabled,
    meals_for_new_event,
    plan_meals_update,
    resolve_event_meals,
    selection_views,
    stored_options,
    validate_meal_selections,
)

ON = {"registration": True, "tickets": False, "sessions": False, "check_in": True, "online_meeting": False,
      "custom_questions": False, "meals": True, "accommodation": False}
OFF = {**ON, "meals": False}


def opt(option_id, name, active=True, date=None, description=None):
    return {"id": option_id, "name": name, "description": description, "date": date, "active": active}


# Phase 2.8: the extra fields stored_options()/build_options() now always include, at their respective
# defaults — stored_options() additionally RESOLVES currency (None -> the event's own, "INR" here since
# event() below never sets one); build_options() does not, so a freshly-built option's currency stays None.
_PRICED_DEFAULTS_READ = {"price": "0.00", "currency": "INR", "capacity": None,
                         "purchase_start_at": None, "purchase_end_at": None, "service_start_at": None, "service_end_at": None}
_PRICED_DEFAULTS_WRITE = {**_PRICED_DEFAULTS_READ, "currency": None}


def event(options=None, modules=ON, meals="unset"):
    stored = {"options": options} if meals == "unset" and options is not None else (None if meals == "unset" else meals)
    return SimpleNamespace(modules=modules, meals=stored, pricing_type="free", price=None, ticket_types=[], sessions=[],
                           delivery_mode="in_person", meeting_link=None, meeting_provider=None, custom_fields=[],
                           custom_values=[], form_configuration_version_id=None)


THREE = [opt("breakfast", "Breakfast"), opt("lunch", "Lunch"), opt("dinner", "Dinner")]


# ===========================================================================
# reading stored config (never raises, never invents)
# ===========================================================================


class TestReading:
    def test_a_legacy_event_has_no_meals_and_they_are_off(self):
        legacy = SimpleNamespace(modules=None, meals=None, pricing_type="free", price=None, ticket_types=[], sessions=[], delivery_mode="in_person",
                                 meeting_link=None, meeting_provider=None, custom_fields=[], custom_values=[], form_configuration_version_id=None)
        assert meals_enabled(legacy) is False
        assert stored_options(legacy) == []
        assert resolve_event_meals(legacy) == {"enabled": False, "options": []}

    def test_enabled_is_modules_meals_and_nothing_else(self):
        assert resolve_event_meals(event(THREE, ON))["enabled"] is True
        assert resolve_event_meals(event(THREE, OFF))["enabled"] is False  # options are kept, the switch is modules.meals
        assert stored_options(event(THREE, OFF)) == [{**o, **_PRICED_DEFAULTS_READ} for o in THREE]

    def test_options_are_returned_normalised(self):
        stored = event([{"id": "a", "name": "  Alpha  ", "description": "  ", "date": "", "active": True}])
        assert stored_options(stored) == [{**opt("a", "Alpha"), **_PRICED_DEFAULTS_READ}]

    @pytest.mark.parametrize("garbage", [
        "text", 7, [], {"options": "text"}, {"options": {"a": 1}}, {"options": [None, 1, "x", []]},
        {"options": [{"id": "a"}]}, {"options": [{"name": "No id"}]}, {"options": [{"id": "", "name": "Empty id"}]},
        {"options": [{"id": "bad id", "name": "Spaces"}]}, {"options": [{"id": "a", "name": "   "}]},
        {"options": [{"id": 5, "name": "Numeric id"}]}, {"options": [{"id": "a", "name": 5}]},
    ])
    def test_malformed_stored_data_is_skipped_never_raised(self, garbage):
        assert stored_options(event(meals=garbage)) == []
        assert resolve_event_meals(event(meals=garbage))["options"] == []

    def test_only_an_explicit_false_retires(self):
        stored = event([{"id": "a", "name": "A", "active": False}, {"id": "b", "name": "B"}, {"id": "c", "name": "C", "active": None}])
        assert [o["active"] for o in stored_options(stored)] == [False, True, True]

    def test_duplicate_stored_ids_keep_the_first(self):
        stored = event([opt("Lunch", "First"), opt("lunch", "Second")])
        assert [o["name"] for o in stored_options(stored)] == ["First"]


# ===========================================================================
# building options: ids, retiring, validation
# ===========================================================================


class TestBuildOptions:
    def test_new_options_without_ids_get_unique_generated_ids(self):
        built = build_options([{"name": "Breakfast"}, {"name": "Lunch"}], [])
        assert len({o["id"] for o in built}) == 2 and all(MEAL_ID_PATTERN.match(o["id"]) for o in built)
        assert [o["active"] for o in built] == [True, True]

    def test_client_supplied_ids_are_kept(self):
        assert [o["id"] for o in build_options([{"id": "breakfast-day-1", "name": "Breakfast"}], [])] == ["breakfast-day-1"]

    def test_a_known_id_keeps_its_identity_when_renamed_redated_or_reordered(self):
        existing = THREE
        built = build_options([{"id": "dinner", "name": "Supper", "date": "2027-01-10"}, {"id": "breakfast", "name": "Breakfast"},
                               {"id": "lunch", "name": "Lunch"}], existing)
        assert [(o["id"], o["name"]) for o in built] == [("dinner", "Supper"), ("breakfast", "Breakfast"), ("lunch", "Lunch")]

    def test_an_option_without_an_id_reuses_the_existing_one_with_the_same_name_and_date(self):
        existing = [opt("b1", "Breakfast", date="2027-01-10"), opt("b2", "Breakfast", date="2027-01-11")]
        built = build_options([{"name": "breakfast", "date": "2027-01-11"}, {"name": "BREAKFAST ", "date": "2027-01-10"}], existing)
        assert [o["id"] for o in built] == ["b2", "b1"]  # name compared case-insensitively, date must match too

    def test_repeated_updates_never_change_ids(self):
        first = build_options([{"name": "A"}, {"name": "B"}], [])
        second = build_options([{"name": "A"}, {"name": "B"}], first)
        third = build_options([{"name": "B"}, {"name": "A"}], second)
        assert [o["id"] for o in second] == [o["id"] for o in first]
        assert sorted(o["id"] for o in third) == sorted(o["id"] for o in first)

    def test_options_an_update_stops_listing_are_retired_not_deleted(self):
        built = build_options([{"id": "breakfast", "name": "Breakfast"}], THREE)
        assert [(o["id"], o["active"]) for o in built] == [("breakfast", True), ("lunch", False), ("dinner", False)]
        assert built[1]["name"] == "Lunch"  # everything else about it survives

    def test_an_empty_list_retires_everything(self):
        assert [o["active"] for o in build_options([], THREE)] == [False, False, False]

    def test_relisting_a_retired_option_reactivates_it_with_its_id(self):
        retired = [opt("lunch", "Lunch", active=False)]
        assert build_options([{"name": "Lunch"}], retired) == [{**opt("lunch", "Lunch"), **_PRICED_DEFAULTS_WRITE}]
        assert build_options([{"id": "lunch", "name": "Lunch"}], retired) == [{**opt("lunch", "Lunch"), **_PRICED_DEFAULTS_WRITE}]

    def test_an_explicit_active_false_retires_a_listed_option(self):
        assert build_options([{"id": "lunch", "name": "Lunch", "active": False}], [])[0]["active"] is False

    def test_duplicate_ids_are_rejected_case_insensitively(self):
        with pytest.raises(MealConfigError, match="Duplicate meal option id"):
            build_options([{"id": "Lunch", "name": "A"}, {"id": "lunch", "name": "B"}], [])

    def test_duplicate_active_options_are_rejected(self):
        with pytest.raises(MealConfigError, match="Duplicate meal option: 'lunch'"):  # names the repeated (second) entry
            build_options([{"name": "Lunch"}, {"name": " lunch "}], [])
        with pytest.raises(MealConfigError, match="on 2027-01-10"):
            build_options([{"name": "Lunch", "date": "2027-01-10"}, {"name": "LUNCH", "date": "2027-01-10"}], [])

    def test_the_same_name_on_different_dates_is_not_a_duplicate(self):
        assert len(build_options([{"name": "Lunch", "date": "2027-01-10"}, {"name": "Lunch", "date": "2027-01-11"}, {"name": "Lunch"}], [])) == 3

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_an_empty_name_is_rejected(self, name):
        with pytest.raises(MealConfigError, match="non-empty name"):
            build_options([{"name": name}], [])

    @pytest.mark.parametrize("bad_id", ["", " ", "has space", "ünï", "a/b", "-leading", "_leading", "x" * 65, 5, ["a"]])
    def test_a_malformed_id_is_rejected(self, bad_id):
        with pytest.raises(MealConfigError, match="ids may only contain"):
            build_options([{"id": bad_id, "name": "Lunch"}], [])

    def test_limits(self):
        with pytest.raises(MealConfigError, match=f"At most {MAX_INPUT_OPTIONS}"):
            build_options([{"name": f"m{i}"} for i in range(MAX_INPUT_OPTIONS + 1)], [])
        history = [opt(f"old{i}", f"Old {i}", active=False) for i in range(MAX_STORED_OPTIONS)]
        with pytest.raises(MealConfigError, match=f"at most {MAX_STORED_OPTIONS}"):
            build_options([{"name": "One more"}], history)


# ===========================================================================
# create + update planning against modules.meals
# ===========================================================================


class TestPlanning:
    def test_new_event_with_no_options_stores_nothing(self):
        assert meals_for_new_event(None, enabled=True) is None
        assert meals_for_new_event([], enabled=False) is None  # nothing to configure, nothing contradicted

    def test_new_event_options_need_meals_on(self):
        with pytest.raises(MealConfigError, match="Meals are disabled"):
            meals_for_new_event([{"name": "Lunch"}], enabled=False)
        stored = meals_for_new_event([{"name": "Lunch"}], enabled=True)
        assert list(stored) == ["options"] and stored["options"][0]["name"] == "Lunch"

    def test_update_with_no_options_is_no_change(self):
        assert plan_meals_update(event(THREE), None, enabled_after=True) is None
        assert plan_meals_update(event(THREE), None, enabled_after=False) is None

    def test_update_changes_are_planned_when_meals_are_on_after_the_update(self):
        planned = plan_meals_update(event(THREE), [{"id": "breakfast", "name": "Brunch"}], enabled_after=True)
        assert [(o["id"], o["name"], o["active"]) for o in planned["options"]] == [
            ("breakfast", "Brunch", True), ("lunch", "Lunch", False), ("dinner", "Dinner", False)]

    def test_changes_are_refused_while_meals_are_off(self):
        with pytest.raises(MealConfigError, match="Meals are disabled"):
            plan_meals_update(event(THREE, OFF), [{"name": "Extra"}], enabled_after=False)

    def test_echoing_the_stored_options_back_is_a_no_op_even_while_off(self):
        echo = [dict(o) for o in THREE]
        assert plan_meals_update(event(THREE, OFF), echo, enabled_after=False) is None
        assert plan_meals_update(event(THREE, ON), echo, enabled_after=True) is None

    def test_a_legacy_event_echoing_no_options_is_a_no_op(self):
        legacy = event(None, modules=None)
        assert plan_meals_update(legacy, [], enabled_after=False) is None


# ===========================================================================
# attendee selections
# ===========================================================================


class TestSelections:
    def test_none_and_empty_mean_no_selection_and_never_need_meals_on(self):
        for selections in (None, []):
            assert validate_meal_selections(event(THREE, ON), selections) is None
            assert validate_meal_selections(event(THREE, OFF), selections) is None
            assert validate_meal_selections(event(None, modules=None), selections) is None

    def test_valid_selections_are_returned_in_the_events_option_order(self):
        assert validate_meal_selections(event(THREE), ["dinner", "breakfast"]) == ["breakfast", "dinner"]

    def test_meals_must_be_on(self):
        with pytest.raises(MealSelectionError, match="not enabled"):
            validate_meal_selections(event(THREE, OFF), ["lunch"])
        with pytest.raises(MealSelectionError, match="not enabled"):
            validate_meal_selections(event(None, modules=None), ["lunch"])  # legacy: no meals

    def test_unknown_ids_are_rejected_and_named(self):
        with pytest.raises(MealSelectionError, match="Unknown meal option.*: brunch, snack"):
            validate_meal_selections(event(THREE), ["lunch", "snack", "brunch"])

    def test_the_configuration_is_the_source_of_truth(self):
        stored = event([opt("breakfast", "Breakfast")])
        with pytest.raises(MealSelectionError, match="Unknown"):
            validate_meal_selections(stored, ["lunch"])  # "lunch": true is refused when no lunch is configured

    def test_ids_are_matched_exactly(self):
        with pytest.raises(MealSelectionError, match="Unknown"):
            validate_meal_selections(event(THREE), ["Lunch"])

    def test_duplicates_are_rejected(self):
        with pytest.raises(MealSelectionError, match="Duplicate meal selection: lunch"):
            validate_meal_selections(event(THREE), ["lunch", "lunch"])

    @pytest.mark.parametrize("bad", [[None], [1], [["lunch"]], [{"id": "lunch"}], ["lunch", None]])
    def test_non_string_items_are_rejected(self, bad):
        with pytest.raises(MealSelectionError, match="list of meal option ids"):
            validate_meal_selections(event(THREE), bad)

    def test_retired_options_cannot_be_newly_selected_but_may_be_kept(self):
        stored = event([opt("breakfast", "Breakfast"), opt("lunch", "Lunch", active=False)])
        with pytest.raises(MealSelectionError, match="no longer available: Lunch"):
            validate_meal_selections(stored, ["lunch"])
        assert validate_meal_selections(stored, ["breakfast", "lunch"], current=["lunch"]) == ["breakfast", "lunch"]
        with pytest.raises(MealSelectionError, match="no longer available"):
            validate_meal_selections(stored, ["lunch"], current=["breakfast"])

    def test_views_carry_names_flags_and_option_order(self):
        options = stored_options(event([opt("breakfast", "Breakfast"), opt("lunch", "Lunch", active=False)]))
        assert selection_views(options, ["lunch", "breakfast"]) == [
            {"meal_id": "breakfast", "name": "Breakfast", "active": True}, {"meal_id": "lunch", "name": "Lunch", "active": False}]

    def test_views_show_unknown_ids_rather_than_hide_them(self):
        options = stored_options(event(THREE))
        assert selection_views(options, ["lunch", "ghost"]) == [
            {"meal_id": "lunch", "name": "Lunch", "active": True}, {"meal_id": "ghost", "name": None, "active": False}]

    @pytest.mark.parametrize("junk", [None, "lunch", 7, {"lunch": True}, [None, 3]])
    def test_views_never_raise_on_odd_stored_data(self, junk):
        assert selection_views(stored_options(event(THREE)), junk) == []


# ===========================================================================
# schemas
# ===========================================================================


class TestSchemas:
    def test_option_input_validation(self):
        assert MealOptionInput(name="  Lunch ").name == "Lunch"
        assert MealOptionInput(name="Lunch", date="2027-01-10").as_dict()["date"] == "2027-01-10"
        for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "A", "id": ""}, {"name": "A", "id": "bad id"},
                    {"name": "A", "id": "x" * 65}, {"name": "A", "date": "tomorrow"}, {"name": "A", "active": "yes"},
                    {"name": "A", "description": "x" * 301}, {"name": "A", "price": -5}, {}, {"id": "a"}):
            with pytest.raises(ValidationError):
                MealOptionInput(**bad)

    def test_meals_input_rules(self):
        assert EventMealsInput().options is None  # nothing sent = no change
        assert EventMealsInput(enabled=True, options=[{"name": "A"}]).option_dicts()[0]["name"] == "A"
        for bad in ({"options": "Lunch"}, {"options": [None]}, {"options": ["Lunch"]}, {"options": {"name": "A"}}, {"enabled": "yes"},
                    {"enabled": 1}, {"vendor": "x"}, {"options": [{"name": f"m{i}"} for i in range(MAX_INPUT_OPTIONS + 1)]}):
            with pytest.raises(ValidationError):
                EventMealsInput(**bad)

    def test_selection_update_rules(self):
        assert EventMealSelectionUpdate(meal_selections=[]).meal_selections == []
        assert EventMealSelectionUpdate(meal_selections=["breakfast-day-1", "uuid-like-0f8a"]).meal_selections
        for bad in ({}, {"meal_selections": None}, {"meal_selections": "lunch"}, {"meal_selections": [None]}, {"meal_selections": [1]},
                    {"meal_selections": ["lunch", "lunch"]}, {"meal_selections": ["bad id"]}, {"meal_selections": [""]},
                    {"meal_selections": ["x"] * 101}, {"meal_selections": ["a"], "tenant_id": "x"}, {"meal_selections": {"lunch": True}}):
            with pytest.raises(ValidationError):
                EventMealSelectionUpdate(**bad)
