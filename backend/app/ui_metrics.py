"""Join UI step occurrences to performance samples for run reports."""

from contextlib import closing
import json
import sqlite3
import time

from .ui_store import TERMINAL


def step_metrics(run: dict, events: list[dict], performance_store) -> list[dict]:
    session_ids = list(dict.fromkeys([run.get('performanceSessionId')] + [e.get('sessionId') for e in events if e.get('type') == 'performance']))
    starts = {}
    intervals = {}
    for event in events:
        if event.get('type') != 'step' or not isinstance(event.get('time'), int):
            continue
        step_id = event.get('stepId')
        occurrence = (step_id, tuple(event.get('iterations') or ()))
        stamp = event['time']
        if event.get('state') == 'running':
            starts[occurrence] = stamp
        elif event.get('state') in TERMINAL:
            started = starts.pop(occurrence, None)
            if started is not None and stamp >= started:
                intervals.setdefault(step_id, []).append((started, stamp))
    # A live occurrence can be shown provisionally. Terminal runs without an
    # occurrence end have no evidence of its duration, including after recovery.
    if run.get('state') in {'queued', 'running'} and not run.get('endedAt'):
        now = int(time.time() * 1000)
        for (step_id, _), started in starts.items():
            if started <= now:
                intervals.setdefault(step_id, []).append((started, now))

    output = []
    for step in run['testCase']['steps']:
        ranges = []
        for start, end in sorted(intervals.get(step['id'], [])):
            if ranges and start <= ranges[-1][1]:
                ranges[-1][1] = max(end, ranges[-1][1])
            else:
                ranges.append([start, end])
        if not ranges:
            continue
        start, end = ranges[0][0], ranges[-1][1]
        windows = json.dumps(ranges)
        for session_id in filter(None, session_ids):
            path = performance_store.database_path(session_id)
            if not path.exists():
                continue
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute('''
                    SELECT COUNT(*) AS sampleCount, AVG(cpu_total_pct) AS cpuAverage,
                           MAX(cpu_total_pct) AS cpuPeak, AVG(pss_kb)/1024.0 AS memoryAverageMb,
                           AVG(fps) AS fpsAverage
                    FROM json_each(?) AS bounds CROSS JOIN sample
                    WHERE ts_ms BETWEEN json_extract(bounds.value, '$[0]')
                                    AND json_extract(bounds.value, '$[1]')
                ''', (windows,)).fetchone()
            if row['sampleCount']:
                output.append({'stepId': step['id'], 'sessionId': session_id, 'start': start, 'end': end, **dict(row)})
    return output
