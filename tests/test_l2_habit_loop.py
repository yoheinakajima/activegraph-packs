"""The L2 Habit substrate closes end to end without keys or ambient time."""

from packs.fixtures.l2_habit_integration import run_l2_habit_fixture


def test_closed_l2_habit_loop() -> None:
    result = run_l2_habit_fixture()
    assert result["skill_used_events"] == 1
    assert result["terminal_outcome"] == "outcome.helped"
    assert result["interaction_utc_dates"] == ["2026-07-09", "2026-07-10"]
    assert result["memory"]["demoted_multiplier"] == 0.1
    assert result["memory"]["restored_multiplier"] == 1.0
