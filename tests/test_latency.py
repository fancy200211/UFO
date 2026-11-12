# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Unit tests for the structured latency instrumentation."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from ufo.utils.latency import (
    LatencyStore,
    LatencyTracker,
    aggregate,
    activate_tracker,
    current_tracker,
    record_stage,
    reset_tracker,
)


def _record(total_s=10.0, **extra):
    """Build a representative step record."""
    tracker = LatencyTracker(role="app", agent="MobileAgent", enabled=True)
    tracker.add("data.screenshot", 0.5)
    tracker.add("data.controls", 2.5)
    tracker.add("llm.prompt", 0.1)
    tracker.add("llm.completion", 5.0)
    tracker.add_action("click_control", 1.0, True)
    tracker.set_llm(model="glm-5.3-flash", prompt_chars=1000, images=2, image_bytes=2048)
    tracker.incr_llm("calls")
    return tracker.as_dict(
        phases={"data_collection": 3.2, "llm_interaction": 5.4, "action_execution": 1.0},
        total_s=total_s,
        **extra,
    )


def test_stage_nesting_only_counts_leaf_stages():
    """A parent stage must not be double counted with its children."""
    tracker = LatencyTracker(role="app", enabled=True)
    tracker.add("llm", 10.0)
    tracker.add("llm.completion", 6.0)

    leaves = tracker.leaf_stages()
    assert "llm" not in leaves
    assert leaves["llm.completion"] == pytest.approx(6.0)

    record = tracker.as_dict(phases={"llm_interaction": 10.0}, total_s=10.0)
    assert record["stages"] == {"llm.completion": 6.0}
    assert record["phase_other"]["llm_interaction"] == pytest.approx(4.0)


def test_unaccounted_time_keeps_the_invariant():
    """phases + unaccounted must equal the step wall-clock."""
    record = _record(total_s=12.0)
    phase_sum = sum(record["phases"].values())
    assert phase_sum + record["unaccounted_s"] == pytest.approx(12.0, abs=1e-6)


def test_phase_other_subtracts_children():
    """The 'other' part of a phase is the phase total minus its stages."""
    record = _record()
    assert record["phase_other"]["data_collection"] == pytest.approx(0.2, abs=1e-6)
    assert record["phase_other"]["llm_interaction"] == pytest.approx(0.3, abs=1e-6)


def test_aggregate_statistics():
    """Aggregation reports sums, percentiles and shares."""
    steps = [_record(total_s=10.0), _record(total_s=30.0)]
    steps[0]["session_step"] = 0
    steps[1]["session_step"] = 1

    summary = aggregate(steps)
    assert summary["step_count"] == 2
    assert summary["total_s"] == pytest.approx(40.0)
    assert summary["phases"]["llm_interaction"]["sum_s"] == pytest.approx(10.8)
    assert summary["phases"]["llm_interaction"]["share"] == pytest.approx(0.27)
    assert summary["stages"]["data.controls"]["p50_s"] == pytest.approx(2.5)
    assert summary["llm"]["calls"] == 2
    assert summary["llm"]["models"] == {"glm-5.3-flash": 2}
    assert summary["actions"]["count"] == 2
    assert summary["slow_steps"][0]["total_s"] == pytest.approx(30.0)


def test_gap_between_steps_is_measured():
    """Inter-step sleep lands in the gap statistics, not in a phase."""
    first = _record(total_s=10.0)
    second = _record(total_s=10.0)
    start = datetime.now(timezone.utc)
    first["started_at"] = start.isoformat()
    second["started_at"] = (start + timedelta(seconds=13)).isoformat()

    summary = aggregate([first, second])
    assert summary["gaps"]["count"] == 1
    assert summary["gaps"]["sum_s"] == pytest.approx(3.0, abs=1e-3)


def test_store_writes_jsonl_json_and_markdown(tmp_path):
    """The store produces the three artifacts and tolerates extra steps."""
    store = LatencyStore(str(tmp_path), enabled=True)
    summary = store.append_step(_record(total_s=10.0))
    store.append_step(_record(total_s=20.0))

    assert (tmp_path / "latency.steps.jsonl").exists()
    assert (tmp_path / "latency.json").exists()
    assert (tmp_path / "latency.md").exists()

    assert summary is not None
    loaded = json.loads((tmp_path / "latency.json").read_text())
    assert loaded["step_count"] == 2
    assert "Phase Breakdown" in (tmp_path / "latency.md").read_text()


def test_store_recovers_partial_jsonl(tmp_path):
    """A truncated last line must not break aggregation."""
    store = LatencyStore(str(tmp_path), enabled=True)
    store.append_step(_record(total_s=10.0))
    with open(tmp_path / "latency.steps.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"total_s": 5.0, "phases": {"llm_')

    steps = store.load_steps()
    assert len(steps) == 1
    assert aggregate(steps)["total_s"] == pytest.approx(10.0)


def test_disabled_tracker_is_a_noop():
    """A disabled tracker records nothing and yields zeroed timings."""
    tracker = LatencyTracker(role="app", enabled=False)
    tracker.add("data.controls", 5.0)
    with tracker.stage("llm.completion"):
        pass
    record = tracker.as_dict(phases={"data_collection": 5.0}, total_s=5.0)
    assert record["stages"] == {}
    assert record["llm"] == {}
    assert record["phase_other"]["data_collection"] == pytest.approx(5.0)


def test_current_tracker_survives_thread_boundaries():
    """The LLM layer runs in a worker thread; the fallback must still resolve."""
    import threading

    tracker = LatencyTracker(role="app", enabled=True)
    token = activate_tracker(tracker)
    try:
        seen = {}

        def worker():
            seen["tracker"] = current_tracker()
            record_stage("llm.completion", 1.5)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert seen["tracker"] is tracker
        assert tracker.leaf_stages()["llm.completion"] == pytest.approx(1.5)
    finally:
        reset_tracker(token, tracker)

    assert current_tracker().enabled is False


def test_old_trace_without_latency_is_ignored(tmp_path):
    """Aggregation tolerates step records that predate this feature."""
    summary = aggregate([{"session_step": 0, "total_s": 3.0}])
    assert summary["step_count"] == 1
    assert summary["total_s"] == pytest.approx(3.0)
    assert summary["stages"] == {}

    store = LatencyStore(str(tmp_path), enabled=True)
    store_path = tmp_path / "latency.steps.jsonl"
    store_path.write_text(json.dumps({"session_step": 0, "total_s": 3.0}) + "\n")
    result = store.append_step(_record(total_s=4.0))
    assert result is not None
    assert result["step_count"] == 2
    assert os.path.getsize(store_path) > 0
