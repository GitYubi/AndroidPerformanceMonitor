"""Bounded periodic TX samples. Timestamps measure host API calls."""

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import json
from math import ceil
from statistics import fmean
from threading import Lock
from time import time_ns

from zlg.paths import data_path


@dataclass(slots=True)
class TxSample:
    task_id: int
    channel: int
    can_id: int
    extended: bool
    can_fd: bool
    period_ns: int
    deadline_ns: int
    entered_ns: int
    lock_acquired_ns: int
    requested_frames: int = 1
    api_start_ns: int | None = None
    api_return_ns: int | None = None
    returned_frames: int | None = None
    skipped_periods: int = 0
    failed: bool = False

    @property
    def lock_wait_ns(self):
        return self.lock_acquired_ns - self.entered_ns

    @property
    def api_duration_ns(self):
        if self.api_start_ns is None or self.api_return_ns is None:
            return None
        return self.api_return_ns - self.api_start_ns


class TxTiming:
    def __init__(self, api, capacity=32_768):
        self.api = api
        self._samples = deque(maxlen=capacity)
        self._count = 0
        self._lock = Lock()

    def record(self, sample):
        with self._lock:
            self._samples.append(sample)
            self._count += 1

    def report(self):
        with self._lock:
            samples = tuple(self._samples)
            count = self._count
        groups = defaultdict(list)
        previous = {}
        rows = []
        for sample in samples:
            key = (sample.channel, sample.can_id, sample.extended, sample.can_fd, sample.period_ns)
            interval = None
            last = previous.get(sample.task_id)
            if sample.api_start_ns is not None:
                if last and last[0] == key:
                    interval = sample.api_start_ns - last[1]
                previous[sample.task_id] = (key, sample.api_start_ns)
            row = asdict(sample)
            row.update(api_interval_ns=interval, api_duration_ns=sample.api_duration_ns,
                       lock_wait_ns=sample.lock_wait_ns,
                       overdue=sample.lock_acquired_ns - sample.deadline_ns >= sample.period_ns)
            rows.append(row)
            groups[key].append(row)

        summary = []
        for (channel, can_id, extended, can_fd, period), group in groups.items():
            intervals = sorted(row["api_interval_ns"] for row in group if row["api_interval_ns"] is not None)
            def percentile(fraction):
                return intervals[ceil(len(intervals) * fraction) - 1] if intervals else None
            summary.append(dict(
                channel=channel, can_id=can_id, extended=extended, can_fd=can_fd, period_ns=period,
                attempts=len(group), intervals=len(intervals),
                mean_ns=fmean(intervals) if intervals else None,
                p50_ns=percentile(0.50), p95_ns=percentile(0.95), p99_ns=percentile(0.99),
                max_ns=max(intervals, default=None),
                over_1_5x=sum(gap * 2 > period * 3 for gap in intervals),
                over_2x=sum(gap > period * 2 for gap in intervals),
                max_lock_wait_ns=max(row["lock_wait_ns"] for row in group),
                max_api_duration_ns=max((row["api_duration_ns"] for row in group
                                         if row["api_duration_ns"] is not None), default=None),
                overdue=sum(row["overdue"] for row in group),
                skipped_periods=sum(row["skipped_periods"] for row in group),
                failures=sum(row["failed"] for row in group),
            ))
        return dict(api=self.api, bus_timestamps=False, retained=len(rows),
                    evicted=count - len(rows), summary=summary, samples=rows)

    def export(self):
        path = data_path("tx_timing") / f"tx-{time_ns()}.json"
        with path.open("w", encoding="utf-8") as file:
            json.dump(self.report(), file, ensure_ascii=False)
        return path
