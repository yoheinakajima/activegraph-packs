"""Synthetic calendar boundaries and real Pack bookkeeping; no wall clock."""
import pytest
from pydantic import ValidationError

from activegraph import Graph, Runtime
from packs.core import pack as core_pack
from packs.schedule import pack as schedule_pack
from packs.schedule.object_types import Schedule
from packs.schedule.tools import compute_next_due, create_schedule_fn, emit_due_ticks_fn


def next_due(kind, after, **spec):
    return compute_next_due(kind, after=after, every_seconds=None, at=None,
                            at_time=spec.pop("at_time", "00:00"), **spec)


@pytest.mark.parametrize("kind,after,spec,expected", [
    ("daily", "2026-01-01T00:00:00Z", {}, "2026-01-02T00:00:00+00:00"),
    ("daily", "2026-01-01T14:59:59Z", {"timezone": "Asia/Seoul"}, "2026-01-01T15:00:00+00:00"),
    ("daily", "2026-01-01T15:00:00Z", {"timezone": "Asia/Seoul"}, "2026-01-02T15:00:00+00:00"),
    ("weekly", "2026-10-09T14:59:59Z", {"weekdays": ["sat"], "timezone": "Asia/Seoul"}, "2026-10-09T15:00:00+00:00"),
    ("weekly", "2026-10-09T15:00:00Z", {"weekdays": ["sat"], "timezone": "Asia/Seoul"}, "2026-10-16T15:00:00+00:00"),
    ("weekly", "2026-10-10T00:00:00Z", {"weekdays": ["fri", "mon"]}, "2026-10-12T00:00:00+00:00"),
    ("weekly", "2026-12-31T00:00:00Z", {"weekdays": ["mon"]}, "2027-01-04T00:00:00+00:00"),
    ("monthly", "2026-01-31T00:00:00Z", {"day_of_month": 31}, "2026-02-28T00:00:00+00:00"),
    ("monthly", "2026-02-28T00:00:00Z", {"day_of_month": 31}, "2026-03-31T00:00:00+00:00"),
    ("monthly", "2028-02-01T00:00:00Z", {"day_of_month": 30}, "2028-02-29T00:00:00+00:00"),
    ("monthly", "2100-02-01T00:00:00Z", {"day_of_month": "last"}, "2100-02-28T00:00:00+00:00"),
    ("monthly", "2026-12-31T00:00:00Z", {"day_of_month": "last"}, "2027-01-31T00:00:00+00:00"),
    ("monthly", "2026-12-31T14:59:59Z", {"day_of_month": 1, "timezone": "Asia/Seoul"}, "2026-12-31T15:00:00+00:00"),
    ("monthly", "2026-03-31T23:00:00-05:00", {"day_of_month": "last", "timezone": "Asia/Seoul"}, "2026-04-29T15:00:00+00:00"),
])
def test_calendar_boundary(kind, after, spec, expected):
    assert next_due(kind, after, **spec) == expected


@pytest.mark.parametrize("spec", [
    {"kind": "weekly"}, {"kind": "weekly", "weekdays": []},
    {"kind": "weekly", "weekdays": ["mon", "mon"]},
    {"kind": "weekly", "weekdays": ["MON"]},
    {"kind": "weekly", "weekdays": [0]},
    {"kind": "weekly", "weekdays": ["mon"], "day_of_month": 1},
    {"kind": "monthly"},
    *({"kind": "monthly", "day_of_month": d} for d in [0, 32, True, 1.0, "1", "LAST"]),
    {"kind": "monthly", "day_of_month": 1, "timezone": "America/New_York"},
    {"kind": "daily", "timezone": "KST"},
    {"kind": "weekly", "weekdays": ["mon"], "at_time": "24:00"},
    {"kind": "weekly", "weekdays": ["mon"], "at_time": "1:00"},
    {"kind": "weekly", "weekdays": ["mon"], "at_time": "12:00\n"},
])
def test_invalid_rule_rejected_before_write(spec):
    rt = Runtime(Graph()); rt.load_pack(core_pack); rt.load_pack(schedule_pack)
    with pytest.raises((ValueError, ValidationError)):
        create_schedule_fn(rt.graph, name="invalid", payload_emit_type="heartbeat",
                           now="2026-01-01T00:00:00Z", **{"at_time": "00:00", **spec})
    assert list(rt.graph.objects(type="schedule")) == []


@pytest.mark.parametrize("kind,spec,first,next_time", [
    ("weekly", {"weekdays": ["sat"], "timezone": "Asia/Seoul"},
     "2026-01-02T15:00:00+00:00", "2026-04-10T15:00:00+00:00"),
    ("monthly", {"day_of_month": 31},
     "2026-01-31T00:00:00+00:00", "2026-04-30T00:00:00+00:00"),
])
def test_calendar_bookkeeper_one_catch_up_dedup_and_disable(kind, spec, first, next_time):
    rt = Runtime(Graph()); rt.load_pack(core_pack); rt.load_pack(schedule_pack)
    obj = create_schedule_fn(rt.graph, name="calendar", kind=kind, at_time="00:00",
                             payload_emit_type="heartbeat", now="2026-01-01T00:00:00Z", **spec)
    rt.run_until_idle()
    assert obj.data["next_due_at"] == first
    late = "2026-04-06T00:00:00Z"
    ticks = emit_due_ticks_fn(rt.graph, late)
    assert len(ticks) == 1
    assert emit_due_ticks_fn(rt.graph, late) == []  # even before bookkeeping
    rt.run_until_idle()
    assert len(list(rt.graph.objects(type="heartbeat"))) == 1
    tick = rt.graph.get_object(ticks[0])
    assert tick.data["due_at"] == first
    current = rt.graph.get_object(obj.id).data
    assert current["next_due_at"] == next_time
    assert current.get("day_of_month") == spec.get("day_of_month")
    assert emit_due_ticks_fn(rt.graph, late) == []
    rt.graph.patch_object(obj.id, {"enabled": False}); rt.run_until_idle()
    assert emit_due_ticks_fn(rt.graph, "2027-01-01T00:00:00Z") == []


def test_legacy_daily_without_calendar_fields():
    old = {"name": "legacy", "kind": "daily", "at_time": "09:30", "payload_emit_type": "heartbeat"}
    assert Schedule.model_validate(old).timezone == "UTC"
    assert "timezone" not in old
    assert next_due("daily", "2026-01-01T09:30:00Z", at_time="09:30") == "2026-01-02T09:30:00+00:00"


def test_legacy_incomplete_and_unpadded_daily():
    assert next_due("daily", "2026-01-01T00:00:00Z", at_time=None) is None
    assert next_due("daily", "2026-01-01T00:00:00Z", at_time="9:30") == "2026-01-01T09:30:00+00:00"
