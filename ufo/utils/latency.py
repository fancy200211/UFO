# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Structured latency instrumentation for UFO3 and Galaxy.

This module is the single source of truth for "where did this step spend its
time".  It is intentionally dependency free (standard library only) and must
never break the agent execution: every public helper degrades to a no-op and
logs at ``debug`` level when something goes wrong.

Design
------
* :class:`LatencyTracker` collects timings for **one** processing step.  It
  separates:

  - ``phases``: the wall-clock duration of every ``ProcessingPhase``.  These
    are measured by ``ProcessorTemplate.process`` and are *inclusive*.
  - ``stages``: leaf timings inside a phase (``data.controls``,
    ``llm.completion``, ``action.wait`` ...).  They are *exclusive* and are
    also used to derive the "other" part of each phase.
  - ``llm`` / ``actions``: non-timing metadata (request profile, retries).

  ``total_s`` is the step wall-clock, so the invariant
  ``sum(phases) + unaccounted_s == total_s`` always holds.

* :class:`LatencyStore` persists one record per step into
  ``latency.steps.jsonl`` (append only, crash safe) and rewrites
  ``latency.json`` plus ``latency.md`` (atomic replace) after every step, so a
  partially finished run still produces usable numbers.

* :func:`current_tracker` exposes the tracker of the running step through a
  :class:`~contextvars.ContextVar`.  That is how the LLM layer
  (``ufo/llm/llm_call.py``, ``ufo/llm/openai.py``) and the data collection
  strategies report timings without threading the tracker through every
  signature.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Canonical phase order, mirrors ``ProcessingPhase``.
PHASE_ORDER: List[str] = [
    "setup",
    "data_collection",
    "llm_interaction",
    "action_execution",
    "memory_update",
    "cleanup",
]

#: Maps a leaf stage prefix to the processing phase that owns it.
_STAGE_PHASE_PREFIX: Dict[str, str] = {
    "data": "data_collection",
    "llm": "llm_interaction",
    "action": "action_execution",
    "memory": "memory_update",
    "setup": "setup",
    "cleanup": "cleanup",
}

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "tracking": True,
    "slow_step_s": 60.0,
    "top_n": 5,
}

#: Hard cap for the number of raw HTTP attempts kept per step.
_MAX_HTTP_ENTRIES = 20


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _as_float(value: Any) -> float:
    """Best effort conversion to float, ``0.0`` on failure."""
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def load_settings() -> Dict[str, Any]:
    """
    Read latency settings from ``config/ufo/system.yaml``.

    :return: Dictionary with ``tracking``, ``slow_step_s`` and ``top_n``.
    """
    settings = dict(_DEFAULT_SETTINGS)
    try:
        from config.config_loader import get_ufo_config

        system = get_ufo_config().system
        settings["tracking"] = bool(system.get("LATENCY_TRACKING", True))
        settings["slow_step_s"] = float(system.get("LATENCY_SLOW_STEP_S", 60.0))
        settings["top_n"] = int(system.get("LATENCY_TOP_N", 5))
    except Exception as error:  # pragma: no cover - config is optional here
        logger.debug("Latency settings fallback to defaults: %s", error)
    return settings


def tracking_enabled() -> bool:
    """Return whether latency tracking is globally enabled."""
    return bool(load_settings().get("tracking", True))


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
class LatencyTracker:
    """
    Collect timings for a single processing step.

    The tracker is thread safe (a step may dispatch work to worker threads) and
    every mutation is guarded so that instrumentation can never raise into the
    agent control flow.
    """

    def __init__(
        self,
        role: str = "app",
        agent: str = "",
        enabled: Optional[bool] = None,
    ) -> None:
        """
        Create a tracker.

        :param role: ``"app"`` or ``"constellation"``.
        :param agent: Human readable agent name, e.g. ``MobileAgent``.
        :param enabled: Force enable/disable; ``None`` reads the config switch.
        """
        self.role = role
        self.agent = agent
        self.enabled = tracking_enabled() if enabled is None else bool(enabled)
        self._lock = threading.Lock()
        self._stages: Dict[str, float] = {}
        self._stage_meta: Dict[str, Dict[str, Any]] = {}
        self._llm: Dict[str, Any] = {}
        self._actions: List[Dict[str, Any]] = []
        self._started_at = _utc_now()

    # -- recording ---------------------------------------------------------- #
    @contextmanager
    def stage(self, name: str, **meta: Any) -> Iterator[None]:
        """
        Measure a leaf stage with a context manager.

        Nested usage is supported: :meth:`as_dict` only aggregates leaf stages
        (a stage with a recorded child) so a step can be timed both coarsely and
        finely without double counting.

        :param name: Stage name, e.g. ``"llm.completion"``.
        :param meta: Optional JSON-safe metadata stored with the stage.
        """
        if not self.enabled:
            yield
            return

        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - started, **meta)

    def add(self, name: str, seconds: Optional[float], **meta: Any) -> None:
        """
        Add an already measured duration to a stage.

        Use this instead of :meth:`stage` when the duration is measured
        elsewhere already (re-measuring would skew the numbers).

        :param name: Stage name.
        :param seconds: Duration in seconds; ``None``/negative values are ignored.
        :param meta: Optional JSON-safe metadata.
        """
        if not self.enabled or not name:
            return
        try:
            value = float(seconds) if seconds is not None else 0.0
        except (TypeError, ValueError):
            return
        if value < 0:
            return
        try:
            with self._lock:
                self._stages[name] = self._stages.get(name, 0.0) + value
                if meta:
                    merged = self._stage_meta.setdefault(name, {})
                    merged.update(_jsonable(meta))
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to record latency stage %s: %s", name, error)

    # -- metadata ----------------------------------------------------------- #
    def set_llm(self, **meta: Any) -> None:
        """
        Set/replace LLM metadata for this step (model, prompt profile, tokens).

        :param meta: JSON-safe key/value pairs.
        """
        if not self.enabled or not meta:
            return
        try:
            with self._lock:
                self._llm.update(_jsonable(meta))
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to record llm metadata: %s", error)

    def incr_llm(self, key: str, delta: float = 1) -> None:
        """
        Increment a numeric LLM counter such as ``calls`` or ``retries``.

        :param key: Counter name.
        :param delta: Increment, defaults to ``1``.
        """
        if not self.enabled or not key:
            return
        try:
            with self._lock:
                current = self._llm.get(key, 0)
                if not isinstance(current, (int, float)):
                    current = 0
                self._llm[key] = current + delta
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to increment llm counter %s: %s", key, error)

    def add_llm_http(self, **entry: Any) -> None:
        """
        Append a raw HTTP attempt (retry index, status, elapsed) to the record.

        :param entry: JSON-safe key/value pairs.
        """
        if not self.enabled or not entry:
            return
        try:
            with self._lock:
                attempts = self._llm.setdefault("http", [])
                if len(attempts) >= _MAX_HTTP_ENTRIES:
                    attempts.pop(0)
                attempts.append(_jsonable(entry))
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to record http attempt: %s", error)

    def add_action(
        self, name: str, seconds: Optional[float], success: Optional[bool] = None
    ) -> None:
        """
        Record one executed action.

        :param name: Action name, e.g. ``click_control``.
        :param seconds: Duration in seconds.
        :param success: Optional execution result.
        """
        if not self.enabled:
            return
        try:
            value = float(seconds) if seconds is not None else 0.0
        except (TypeError, ValueError):
            return
        try:
            with self._lock:
                self._actions.append(
                    {
                        "name": name,
                        "s": round(max(value, 0.0), 4),
                        "success": success,
                    }
                )
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to record action latency: %s", error)

    # -- serialisation ------------------------------------------------------ #
    def leaf_stages(self) -> Dict[str, float]:
        """
        Return only leaf stages (stages without a recorded child).

        :return: Mapping of stage name to accumulated seconds.
        """
        with self._lock:
            stages = dict(self._stages)
        leaves: Dict[str, float] = {}
        names = list(stages)
        for name, seconds in stages.items():
            prefix = name + "."
            if any(other.startswith(prefix) for other in names):
                continue
            leaves[name] = seconds
        return leaves

    def as_dict(
        self,
        phases: Optional[Dict[str, float]] = None,
        total_s: Optional[float] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """
        Build the JSON-safe latency record for this step.

        :param phases: Inclusive phase durations, keyed by ``ProcessingPhase``
            value.  Missing phases default to ``0``.
        :param total_s: Step wall-clock in seconds.  Defaults to the sum of
            ``phases``.
        :param extra: Additional JSON-safe fields (``session_step`` etc.).
        :return: Step latency dictionary.
        """
        try:
            phase_values: Dict[str, float] = {}
            raw_phases = phases or {}
            for phase in PHASE_ORDER:
                phase_values[phase] = round(_as_float(raw_phases.get(phase)), 4)
            for phase, value in raw_phases.items():
                if phase not in phase_values:
                    phase_values[phase] = round(_as_float(value), 4)

            leaves = self.leaf_stages() if self.enabled else {}
            stages = {name: round(value, 4) for name, value in leaves.items()}

            phase_other: Dict[str, float] = {}
            children_by_phase = _children_by_phase(leaves)
            for phase in PHASE_ORDER:
                if phase not in phase_values:
                    continue
                children = children_by_phase.get(phase, 0.0)
                phase_other[phase] = round(max(phase_values[phase] - children, 0.0), 4)
            for phase, value in phase_values.items():
                if phase in phase_other:
                    continue
                children = children_by_phase.get(phase, 0.0)
                phase_other[phase] = round(max(value - children, 0.0), 4)

            phase_total = sum(phase_values.values())
            step_total = _as_float(total_s) if total_s is not None else phase_total

            with self._lock:
                llm = _jsonable(dict(self._llm))
                actions = [dict(action) for action in self._actions]

            record: Dict[str, Any] = {
                "schema": SCHEMA_VERSION,
                "role": self.role,
                "agent": self.agent,
                "started_at": self._started_at,
                "total_s": round(step_total, 4),
                "phases": phase_values,
                "phase_other": phase_other,
                "stages": stages,
                "stage_meta": _jsonable(dict(self._stage_meta)),
                "llm": llm,
                "actions": actions,
                "unaccounted_s": round(max(step_total - phase_total, 0.0), 4),
            }
            record.update(_jsonable(extra))
            return record
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to build latency record: %s", error)
            return {
                "schema": SCHEMA_VERSION,
                "role": self.role,
                "agent": self.agent,
                "started_at": self._started_at,
                "total_s": round(_as_float(total_s), 4),
                "phases": {},
                "phase_other": {},
                "stages": {},
                "llm": {},
                "actions": [],
                "unaccounted_s": 0.0,
            }


class _NullTracker(LatencyTracker):
    """Disabled tracker used whenever tracking is off or no step is active."""

    def __init__(self) -> None:  # noqa: D107 - simple no-op
        super().__init__(role="noop", agent="", enabled=False)


_NULL_TRACKER = _NullTracker()
_CURRENT_TRACKER: ContextVar[Optional[LatencyTracker]] = ContextVar(
    "ufo_latency_tracker", default=None
)

#: Fallback for code that runs outside the asyncio context, e.g. the blocking
#: LLM call dispatched through ``run_in_executor``: context variables are not
#: propagated into the worker thread, but UFO processes one step at a time, so
#: the most recently activated tracker is the correct owner.
_ACTIVE_TRACKERS: List[LatencyTracker] = []
_ACTIVE_LOCK = threading.Lock()


def current_tracker() -> LatencyTracker:
    """
    Return the tracker of the step being processed.

    :return: Active tracker, or a no-op tracker outside of a step.
    """
    tracker = _CURRENT_TRACKER.get()
    if tracker is not None:
        return tracker
    with _ACTIVE_LOCK:
        if _ACTIVE_TRACKERS:
            return _ACTIVE_TRACKERS[-1]
    return _NULL_TRACKER


def activate_tracker(tracker: Optional[LatencyTracker]):
    """
    Bind a tracker to the current context and to the process-wide fallback.

    :param tracker: Tracker to activate, ``None`` to clear.
    :return: Token that must be passed to :func:`reset_tracker`.
    """
    if tracker is not None:
        with _ACTIVE_LOCK:
            _ACTIVE_TRACKERS.append(tracker)
    return _CURRENT_TRACKER.set(tracker)


def reset_tracker(token: Any, tracker: Optional[LatencyTracker] = None) -> None:
    """
    Restore the previously bound tracker.

    :param token: Token returned by :func:`activate_tracker`.
    :param tracker: Tracker to drop from the fallback registry.
    """
    try:
        _CURRENT_TRACKER.reset(token)
    except Exception as error:  # pragma: no cover - defensive
        logger.debug("Failed to reset latency tracker: %s", error)
    if tracker is not None:
        with _ACTIVE_LOCK:
            try:
                _ACTIVE_TRACKERS.remove(tracker)
            except ValueError:
                pass


# Convenience helpers so hot paths stay readable.
def record_stage(name: str, seconds: Optional[float], **meta: Any) -> None:
    """Add a duration to the current step tracker."""
    current_tracker().add(name, seconds, **meta)


def set_llm_meta(**meta: Any) -> None:
    """Attach LLM metadata to the current step tracker."""
    current_tracker().set_llm(**meta)


def incr_llm(key: str, delta: float = 1) -> None:
    """Increment an LLM counter on the current step tracker."""
    current_tracker().incr_llm(key, delta)


def add_llm_http(**entry: Any) -> None:
    """Record a raw HTTP attempt on the current step tracker."""
    current_tracker().add_llm_http(**entry)


def add_action(name: str, seconds: Optional[float], success: Optional[bool] = None) -> None:
    """Record an executed action on the current step tracker."""
    current_tracker().add_action(name, seconds, success)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class LatencyStore:
    """
    Persist step latency records for one log directory.

    Writes are incremental (``latency.steps.jsonl``) plus a rewritten rollup
    (``latency.json`` / ``latency.md``).  Every method swallows its own errors:
    losing telemetry must never fail a task.
    """

    def __init__(self, log_dir: str, enabled: Optional[bool] = None) -> None:
        """
        Create a store bound to a log directory.

        :param log_dir: Task/session log directory (``logs/<task>/``).
        :param enabled: Force enable/disable, ``None`` reads the config switch.
        """
        self.log_dir = log_dir
        self.enabled = bool(log_dir) and (
            tracking_enabled() if enabled is None else bool(enabled)
        )

    @property
    def steps_path(self) -> str:
        """Path of the append-only step log."""
        return os.path.join(self.log_dir, "latency.steps.jsonl")

    @property
    def json_path(self) -> str:
        """Path of the aggregated JSON rollup."""
        return os.path.join(self.log_dir, "latency.json")

    @property
    def markdown_path(self) -> str:
        """Path of the human readable report."""
        return os.path.join(self.log_dir, "latency.md")

    def append_step(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Persist one step record and refresh the rollup.

        :param record: Step latency dictionary from :meth:`LatencyTracker.as_dict`.
        :return: Aggregated summary, or ``None`` when disabled/failed.
        """
        if not self.enabled:
            return None
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            line = json.dumps(_jsonable(record), ensure_ascii=False)
            with open(self.steps_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

            steps = self.load_steps()
            summary = aggregate(steps)
            summary["log_dir"] = self.log_dir
            _atomic_write_text(
                self.json_path, json.dumps(summary, ensure_ascii=False, indent=2)
            )
            _atomic_write_text(self.markdown_path, render_markdown(summary, steps))
            return summary
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to persist latency record: %s", error)
            return None

    def load_steps(self) -> List[Dict[str, Any]]:
        """
        Load all step records written so far.

        :return: List of step dictionaries, tolerating truncated lines.
        """
        steps: List[Dict[str, Any]] = []
        try:
            with open(self.steps_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        steps.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            return []
        except Exception as error:  # pragma: no cover - defensive
            logger.debug("Failed to read latency steps: %s", error)
        return steps


def _atomic_write_text(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(dir=directory, prefix=".latency-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _percentile(values: List[float], quantile: float) -> float:
    """Nearest-rank percentile (no interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = int(math.ceil(quantile * len(ordered))) - 1
    index = min(max(index, 0), len(ordered) - 1)
    return ordered[index]


def _stats(values: List[float], total: float) -> Dict[str, float]:
    """Build sum/mean/p50/p95/share statistics for a list of durations."""
    if not values:
        return {
            "count": 0,
            "sum_s": 0.0,
            "mean_s": 0.0,
            "p50_s": 0.0,
            "p95_s": 0.0,
            "max_s": 0.0,
            "share": 0.0,
        }
    summed = sum(values)
    return {
        "count": len(values),
        "sum_s": round(summed, 4),
        "mean_s": round(summed / len(values), 4),
        "p50_s": round(_percentile(values, 0.5), 4),
        "p95_s": round(_percentile(values, 0.95), 4),
        "max_s": round(max(values), 4),
        "share": round(summed / total, 4) if total else 0.0,
    }


def aggregate(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate step records into a rollup summary.

    :param steps: Step records.
    :return: Summary dictionary with per-phase, per-stage and LLM statistics.
    """
    total_s = sum(_as_float(step.get("total_s")) for step in steps)
    phase_values: Dict[str, List[float]] = {phase: [] for phase in PHASE_ORDER}
    phase_other: Dict[str, List[float]] = {phase: [] for phase in PHASE_ORDER}
    stage_values: Dict[str, List[float]] = {}
    action_values: List[float] = []
    unaccounted: List[float] = []

    llm_calls = 0
    llm_retries = 0
    prompt_chars = 0
    completion_chars = 0
    image_bytes = 0
    images = 0
    models: Dict[str, int] = {}
    http_attempts: List[Dict[str, Any]] = []

    for step in steps:
        for phase, value in (step.get("phases") or {}).items():
            phase_values.setdefault(phase, []).append(_as_float(value))
        for phase, value in (step.get("phase_other") or {}).items():
            phase_other.setdefault(phase, []).append(_as_float(value))
        for name, value in (step.get("stages") or {}).items():
            stage_values.setdefault(name, []).append(_as_float(value))
        for action in step.get("actions") or []:
            action_values.append(_as_float(action.get("s")))
        unaccounted.append(_as_float(step.get("unaccounted_s")))

        llm = step.get("llm") or {}
        llm_calls += int(_as_float(llm.get("calls")))
        llm_retries += int(_as_float(llm.get("retries")))
        prompt_chars += int(_as_float(llm.get("prompt_chars")))
        completion_chars += int(_as_float(llm.get("completion_chars")))
        image_bytes += int(_as_float(llm.get("image_bytes")))
        images += int(_as_float(llm.get("images")))
        model = llm.get("model")
        if model:
            models[str(model)] = models.get(str(model), 0) + 1
        for attempt in llm.get("http") or []:
            if isinstance(attempt, dict):
                http_attempts.append(attempt)

    summary: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "step_count": len(steps),
        "total_s": round(total_s, 4),
        "phases": {
            phase: _stats(phase_values.get(phase, []), total_s)
            for phase in phase_values
        },
        "phase_other": {
            phase: round(sum(phase_other.get(phase, [])), 4)
            for phase in phase_other
        },
        "stages": {
            name: _stats(values, total_s)
            for name, values in sorted(
                stage_values.items(), key=lambda item: -sum(item[1])
            )
        },
        "actions": {
            "count": len(action_values),
            "sum_s": round(sum(action_values), 4),
            "share": round(sum(action_values) / total_s, 4) if total_s else 0.0,
        },
        "unaccounted_s": round(sum(unaccounted), 4),
        "llm": {
            "calls": llm_calls,
            "retries": llm_retries,
            "models": models,
            "prompt_chars": prompt_chars,
            "completion_chars": completion_chars,
            "images": images,
            "image_bytes": image_bytes,
            "http_attempts": len(http_attempts),
        },
    }
    summary["slow_steps"] = _rank_steps(steps)
    summary["outliers"] = _find_outliers(steps, load_settings()["slow_step_s"])
    summary["gaps"] = _gap_stats(steps)
    return summary


def _parse_ts(value: Any) -> Optional[float]:
    """Parse an ISO-8601 timestamp into epoch seconds."""
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def _gap_stats(steps: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Measure the wall-clock between the end of one step and the start of the next.

    This is where the fixed inter-step sleep and the round loop overhead live;
    they are outside ``ProcessorTemplate.process`` and would otherwise only
    show up as "unaccounted" time.
    """
    gaps: List[float] = []
    for previous, current in zip(steps, steps[1:]):
        start_prev = _parse_ts(previous.get("started_at"))
        start_next = _parse_ts(current.get("started_at"))
        if start_prev is None or start_next is None:
            continue
        gap = start_next - start_prev - _step_total(previous)
        if gap >= 0:
            gaps.append(gap)
    return _stats(gaps, sum(gaps))


def _step_total(step: Dict[str, Any]) -> float:
    """Return the effective total wall-clock of a step record."""
    total = _as_float(step.get("total_s"))
    if total:
        return total
    return _as_float((step.get("phases") or {}).get("llm_interaction"))


def _rank_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the slowest steps, most expensive first."""
    top_n = max(int(load_settings().get("top_n", 5)), 1)
    ranked = sorted(steps, key=_step_total, reverse=True)[:top_n]
    result: List[Dict[str, Any]] = []
    for step in ranked:
        stages = step.get("stages") or {}
        dominant = max(stages.items(), key=lambda item: item[1], default=("", 0.0))
        result.append(
            {
                "session_step": step.get("session_step"),
                "round_step": step.get("round_step"),
                "total_s": round(_step_total(step), 4),
                "dominant_stage": dominant[0],
                "dominant_s": round(_as_float(dominant[1]), 4),
                "llm_s": round(_as_float(stages.get("llm.completion")), 4),
                "llm_calls": int(_as_float((step.get("llm") or {}).get("calls"))),
            }
        )
    return result


def _find_outliers(steps: List[Dict[str, Any]], slow_step_s: float) -> List[Dict[str, Any]]:
    """Flag steps above the absolute threshold or 2 sigma above the mean."""
    totals = [_step_total(step) for step in steps]
    if not totals:
        return []
    mean = sum(totals) / len(totals)
    variance = sum((value - mean) ** 2 for value in totals) / len(totals)
    sigma = math.sqrt(variance)

    outliers: List[Dict[str, Any]] = []
    for step, total in zip(steps, totals):
        reasons: List[str] = []
        if slow_step_s and total >= slow_step_s:
            reasons.append(f">={slow_step_s:g}s")
        if len(totals) >= 3 and sigma and total > mean + 2 * sigma:
            reasons.append(">mean+2sigma")
        if reasons:
            outliers.append(
                {
                    "session_step": step.get("session_step"),
                    "total_s": round(total, 4),
                    "ratio_to_mean": round(total / mean, 2) if mean else None,
                    "reason": ",".join(reasons),
                }
            )
    return sorted(outliers, key=lambda item: -item["total_s"])


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt(value: Any) -> str:
    """Format a duration for markdown tables."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def render_markdown(summary: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    """
    Render a readable latency report.

    :param summary: Output of :func:`aggregate`.
    :param steps: Step records, used for the per-step matrix.
    :return: Markdown document.
    """
    total = _as_float(summary.get("total_s"))
    lines: List[str] = []
    lines.append("# Latency Report")
    lines.append("")
    lines.append(f"- Steps: **{summary.get('step_count', 0)}**")
    lines.append(f"- Total wall-clock: **{total:.2f}s**")
    lines.append(f"- LLM calls: **{(summary.get('llm') or {}).get('calls', 0)}**")
    lines.append(f"- Generated: {summary.get('generated_at', '')}")
    lines.append("")

    lines.append("## Phase Breakdown")
    lines.append("")
    lines.append("| Phase | Sum (s) | Mean (s) | p50 (s) | p95 (s) | Share | Other (s) |")
    lines.append("|---|---|---|---|---|---|---|")
    for phase in PHASE_ORDER:
        stats = (summary.get("phases") or {}).get(phase)
        if not stats:
            continue
        other = (summary.get("phase_other") or {}).get(phase, 0.0)
        lines.append(
            "| {phase} | {sum_s} | {mean_s} | {p50_s} | {p95_s} | {share} | {other} |".format(
                phase=phase,
                sum_s=_fmt(stats.get("sum_s")),
                mean_s=_fmt(stats.get("mean_s")),
                p50_s=_fmt(stats.get("p50_s")),
                p95_s=_fmt(stats.get("p95_s")),
                share=f"{_as_float(stats.get('share')) * 100:.1f}%",
                other=_fmt(other),
            )
        )
    lines.append("")

    stages = summary.get("stages") or {}
    if stages:
        lines.append("## Stage Breakdown (leaf stages)")
        lines.append("")
        lines.append("| Stage | Sum (s) | Mean (s) | p50 (s) | p95 (s) | Share |")
        lines.append("|---|---|---|---|---|---|")
        for name, stats in stages.items():
            lines.append(
                "| {name} | {sum_s} | {mean_s} | {p50_s} | {p95_s} | {share} |".format(
                    name=name,
                    sum_s=_fmt(stats.get("sum_s")),
                    mean_s=_fmt(stats.get("mean_s")),
                    p50_s=_fmt(stats.get("p50_s")),
                    p95_s=_fmt(stats.get("p95_s")),
                    share=f"{_as_float(stats.get('share')) * 100:.1f}%",
                )
            )
        lines.append("")

    gaps = summary.get("gaps") or {}
    if gaps.get("count"):
        lines.append("## Inter-step Gaps")
        lines.append("")
        lines.append(
            "Time outside ``process()`` between two consecutive steps "
            "(inter-step sleep, round loop overhead)."
        )
        lines.append("")
        lines.append("| Gap count | Sum (s) | Mean (s) | p50 (s) | p95 (s) | Max (s) |")
        lines.append("|---|---|---|---|---|---|")
        lines.append(
            f"| {gaps.get('count')} | {_fmt(gaps.get('sum_s'))} | "
            f"{_fmt(gaps.get('mean_s'))} | {_fmt(gaps.get('p50_s'))} | "
            f"{_fmt(gaps.get('p95_s'))} | {_fmt(gaps.get('max_s'))} |"
        )
        lines.append("")

    llm = summary.get("llm") or {}
    if llm:
        lines.append("## LLM Profile")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Models | {llm.get('models') or {}} |")
        lines.append(f"| Calls | {llm.get('calls', 0)} |")
        lines.append(f"| Retries | {llm.get('retries', 0)} |")
        lines.append(f"| Prompt chars | {llm.get('prompt_chars', 0)} |")
        lines.append(f"| Completion chars | {llm.get('completion_chars', 0)} |")
        lines.append(f"| Images | {llm.get('images', 0)} |")
        lines.append(f"| Image bytes | {llm.get('image_bytes', 0)} |")
        lines.append("")

    slow_steps = summary.get("slow_steps") or []
    if slow_steps:
        lines.append("## Slowest Steps")
        lines.append("")
        lines.append("| Step | Total (s) | Dominant stage | Dominant (s) | LLM (s) | LLM calls |")
        lines.append("|---|---|---|---|---|---|")
        for item in slow_steps:
            lines.append(
                "| {step} | {total} | {dominant} | {dominant_s} | {llm_s} | {calls} |".format(
                    step=item.get("session_step"),
                    total=_fmt(item.get("total_s")),
                    dominant=item.get("dominant_stage") or "-",
                    dominant_s=_fmt(item.get("dominant_s")),
                    llm_s=_fmt(item.get("llm_s")),
                    calls=item.get("llm_calls"),
                )
            )
        lines.append("")

    outliers = summary.get("outliers") or []
    if outliers:
        lines.append("## Outliers")
        lines.append("")
        lines.append("| Step | Total (s) | vs mean | Reason |")
        lines.append("|---|---|---|---|")
        for item in outliers:
            lines.append(
                f"| {item.get('session_step')} | {_fmt(item.get('total_s'))} | "
                f"{item.get('ratio_to_mean')}x | {item.get('reason')} |"
            )
        lines.append("")

    if steps:
        lines.append("## Per-step Detail")
        lines.append("")
        columns = [
            "data.screenshot",
            "data.apps",
            "data.controls",
            "llm.prompt",
            "llm.completion",
            "llm.parse",
            "action.execute",
            "action.wait",
        ]
        present = [
            column
            for column in columns
            if any(column in (step.get("stages") or {}) for step in steps)
        ]
        header = "| Step | Total | " + " | ".join(present) + " | Unaccounted |"
        lines.append(header)
        lines.append("|---" * (len(present) + 3) + "|")
        for step in steps:
            stages = step.get("stages") or {}
            cells = [_fmt(stages.get(column) if column in stages else None) for column in present]
            lines.append(
                "| {step} | {total} | {cells} | {unaccounted} |".format(
                    step=step.get("session_step"),
                    total=_fmt(step.get("total_s")),
                    cells=" | ".join(cells),
                    unaccounted=_fmt(step.get("unaccounted_s")),
                )
            )
        lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Session level summary (Galaxy)
# --------------------------------------------------------------------------- #
def write_session_latency(
    log_dir: str,
    session_total_s: float,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Write the session level rollup for a Galaxy run.

    The session rollup answers "how much time was spent outside the task
    executions" (constellation planning/editing rounds, container overhead).

    :param log_dir: Session log directory (``logs/galaxy/<session>/``).
    :param session_total_s: Wall-clock duration of the whole session.
    :param tasks: List of ``{"task_id", "name", "duration_s"}`` dictionaries.
    :return: Session summary, or ``None`` when disabled/failed.
    """
    if not log_dir or not tracking_enabled():
        return None
    try:
        steps = LatencyStore(log_dir).load_steps()
        constellation = aggregate(steps)
        task_list = []
        for task in tasks or []:
            task_list.append(
                {
                    "task_id": task.get("task_id"),
                    "name": task.get("name"),
                    "duration_s": round(_as_float(task.get("duration_s")), 4),
                }
            )
        tasks_total = sum(item["duration_s"] for item in task_list)
        constellation_total = _as_float(constellation.get("total_s"))
        summary = {
            "schema": SCHEMA_VERSION,
            "generated_at": _utc_now(),
            "role": "session",
            "log_dir": log_dir,
            "session_total_s": round(_as_float(session_total_s), 4),
            "constellation": constellation,
            "tasks": task_list,
            "tasks_total_s": round(tasks_total, 4),
            "untracked_s": round(
                max(_as_float(session_total_s) - tasks_total - constellation_total, 0.0), 4
            ),
        }
        _atomic_write_text(
            os.path.join(log_dir, "latency.json"),
            json.dumps(_jsonable(summary), ensure_ascii=False, indent=2),
        )
        _atomic_write_text(
            os.path.join(log_dir, "latency.md"), render_session_markdown(summary)
        )
        return summary
    except Exception as error:  # pragma: no cover - defensive
        logger.debug("Failed to write session latency: %s", error)
        return None


def render_session_markdown(summary: Dict[str, Any]) -> str:
    """
    Render the Galaxy session rollup.

    :param summary: Output of :func:`write_session_latency`.
    :return: Markdown document.
    """
    session_total = _as_float(summary.get("session_total_s"))
    constellation = summary.get("constellation") or {}
    lines: List[str] = [
        "# Session Latency Report",
        "",
        f"- Session wall-clock: **{session_total:.2f}s**",
        f"- Tasks: **{len(summary.get('tasks') or [])}**",
        f"- Task execution total: **{_as_float(summary.get('tasks_total_s')):.2f}s**",
        f"- Constellation rounds: **{_as_float(constellation.get('total_s')):.2f}s**",
        f"- Untracked (orchestration/overhead): **{_as_float(summary.get('untracked_s')):.2f}s**",
        f"- Generated: {summary.get('generated_at', '')}",
        "",
    ]
    steps = summary.get("tasks") or []
    if steps:
        lines.append("## Tasks")
        lines.append("")
        lines.append("| Task | Duration (s) | Share |")
        lines.append("|---|---|---|")
        for task in steps:
            duration = _as_float(task.get("duration_s"))
            share = (duration / session_total * 100) if session_total else 0.0
            lines.append(
                f"| {task.get('task_id')} ({task.get('name') or '-'}) | "
                f"{duration:.2f} | {share:.1f}% |"
            )
        lines.append("")
    if constellation:
        lines.append("## Constellation Rounds")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Rounds | {constellation.get('step_count', 0)} |")
        lines.append(f"| Total (s) | {_fmt(constellation.get('total_s'))} |")
        lines.append(f"| LLM calls | {(constellation.get('llm') or {}).get('calls', 0)} |")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _children_by_phase(stages: Dict[str, float]) -> Dict[str, float]:
    """Sum leaf stage durations grouped by their owning phase."""
    grouped: Dict[str, float] = {}
    for name, seconds in stages.items():
        prefix = name.split(".", 1)[0]
        phase = _STAGE_PHASE_PREFIX.get(prefix)
        if not phase:
            continue
        grouped[phase] = grouped.get(phase, 0.0) + seconds
    return grouped


def _jsonable(value: Any) -> Any:
    """Recursively coerce a value into something ``json.dumps`` accepts."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)
