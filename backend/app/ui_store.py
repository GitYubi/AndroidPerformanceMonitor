"""UI case, run, image and event persistence in the existing SQLite schema."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .ui_images import UIImages

if TYPE_CHECKING:
    from .ui_testing import TestCase

TERMINAL = {'passed', 'failed', 'error', 'cancelled', 'interrupted', 'needs_review'}


class UIStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / 'ui.db'
        self.baselines = root / 'baselines'
        self.images = UIImages(root, self.db)
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, body TEXT NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq)')

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def validate_baselines(self, test_case: TestCase):
        self.images.require_project(test_case.projectId)
        files = {}
        for step in test_case.steps:
            operation = step.condition or step
            if getattr(operation.options, "baselineId", None):
                image_id = operation.options.baselineId
                project_id = getattr(operation.options, 'baselineProjectId', None)
                path = self.images.path(image_id, project_id)
                if not path.is_file():
                    raise ValueError('用例引用的 PNG 图片不存在，请重新选择')
                key = f'{project_id}/{image_id}' if project_id else image_id
                files[key] = str(path.resolve())
        return files

    def all(self, table: str):
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(f'SELECT body FROM {table} ORDER BY rowid DESC LIMIT 500')]

    def get(self, table: str, key: str):
        with self.connect() as conn:
            row = conn.execute(f'SELECT body FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def put(self, table: str, body: dict):
        with self.connect() as conn:
            conn.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (body['id'], json.dumps(body, ensure_ascii=False)))

    def event(self, run_id: str, body: dict):
        with self.connect() as conn:
            conn.execute('INSERT INTO events(run_id,body) VALUES (?,?)', (run_id, json.dumps(body, ensure_ascii=False)))

    def events(self, run_id: str, after: int = 0, limit: int = -1):
        with self.connect() as conn:
            return [{'seq': row[0], **json.loads(row[1])} for row in conn.execute(
                'SELECT seq,body FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (run_id, after, limit))]

    def recover(self):
        with self.connect() as conn:
            rows = conn.execute('SELECT body FROM runs').fetchall()
        for row in rows:
            run = json.loads(row[0])
            if run['state'] not in TERMINAL:
                run.update(state='interrupted', endedAt=int(time.time() * 1000))
                self.put('runs', run)
