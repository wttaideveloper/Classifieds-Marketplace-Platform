from app.services.program_service import normalize_program_phases, get_program_goals_service, list_program_surveys_service


def test_normalize_program_phases_includes_nested_arrays():
    phases = [
        {
            "id": "phase-1",
            "title": "Week 1",
            "activities": [{"id": "a1", "title": "Intro"}],
        }
    ]
    normalized = normalize_program_phases(phases)
    assert len(normalized) == 1
    assert normalized[0]["activities"] == [{"id": "a1", "title": "Intro"}]
    assert normalized[0]["instructors"] == []
    assert normalized[0]["coaches"] == []


def test_normalize_program_phases_preserves_instructors():
    phases = [{"id": "p1", "instructors": ["user-1"], "mentors": ["m1"]}]
    normalized = normalize_program_phases(phases)
    assert normalized[0]["instructors"] == ["user-1"]
    assert normalized[0]["mentors"] == ["m1"]
    assert normalized[0]["activities"] == []


def test_get_program_goals_shape():
    class Prog:
        goals = {"baseline": {"weight": 80}, "expected_outcomes": {"fitness": "high"}}

    class DB:
        pass

    from app.services import program_service

    original = program_service.get_program_by_id
    program_service.get_program_by_id = lambda db, pid, include_deleted=False: Prog()
    try:
        result = get_program_goals_service(DB(), "00000000-0000-4000-8000-000000000001")
        assert "goals" in result
        assert result["goals"]["baseline"]["weight"] == 80
    finally:
        program_service.get_program_by_id = original
