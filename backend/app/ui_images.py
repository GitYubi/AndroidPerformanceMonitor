"""Project catalog and immutable PNG assets for UI automation."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import struct
import uuid
from pathlib import Path

from PIL import Image

DEFAULT_PROJECT = 'default'
PROJECT_ID = r'^(?:default|[a-f0-9]{32})$'
IMAGE_ID = r'^[a-f0-9]{64}$'
MAX_PNG = 8 * 1024 * 1024


def png_size(data: bytes):
    if len(data) < 33 or data[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR':
        raise ValueError('请选择 PNG 图片')
    width, height = struct.unpack('>II', data[16:24])
    if not width or not height or width * height > 16_000_000:
        raise ValueError('图片尺寸无效或过大')
    return width, height


class UIImages:
    def __init__(self, root: Path, db: Path):
        self.root = root / 'projects'
        self.legacy = root / 'baselines'
        self.db = db
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS ui_projects (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE)')
            conn.execute('''CREATE TABLE IF NOT EXISTS ui_images (
                project_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL, source TEXT NOT NULL,
                PRIMARY KEY (project_id, id))''')
            if 'region' not in {row[1] for row in conn.execute('PRAGMA table_info(ui_images)')}:
                conn.execute('ALTER TABLE ui_images ADD COLUMN region TEXT')
            if 'hidden' not in {row[1] for row in conn.execute('PRAGMA table_info(ui_images)')}:
                conn.execute('ALTER TABLE ui_images ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0')
            conn.execute('INSERT OR IGNORE INTO ui_projects VALUES (?, ?)', (DEFAULT_PROJECT, '默认项目'))

    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def projects(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute('SELECT id,name FROM ui_projects ORDER BY id != ?, name', (DEFAULT_PROJECT,))]

    def require_project(self, project_id):
        if not re.fullmatch(PROJECT_ID, project_id):
            raise ValueError('项目不存在')
        with self.connect() as conn:
            if not conn.execute('SELECT 1 FROM ui_projects WHERE id=?', (project_id,)).fetchone():
                raise ValueError('项目不存在')

    def create_project(self, name):
        name = name.strip()
        if not name:
            raise ValueError('请输入项目名称')
        project_id = uuid.uuid4().hex
        try:
            with self.connect() as conn:
                conn.execute('INSERT INTO ui_projects VALUES (?, ?)', (project_id, name))
        except sqlite3.IntegrityError as exc:
            raise ValueError('项目名称已存在') from exc
        return {'id': project_id, 'name': name}

    def path(self, image_id, project_id=None):
        if not re.fullmatch(IMAGE_ID, image_id):
            raise ValueError('图片标识无效')
        if project_id is None:
            return self.legacy / f'{image_id}.png'
        if not re.fullmatch(PROJECT_ID, project_id):
            raise ValueError('项目不存在')
        return self.root / project_id / 'images' / f'{image_id}.png'

    def list_images(self, project_id):
        self.require_project(project_id)
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                'SELECT id,name,width,height,source,region,hidden FROM ui_images WHERE project_id=? ORDER BY rowid DESC',
                (project_id,))]
        images = [row.copy() for row in rows if not row['hidden']]
        for item in images:
            item.pop('hidden')
            item['region'] = json.loads(item['region']) if item['region'] else None
            item['aliases'] = [row['id'] for row in rows if row.get('hidden') and row['name'] == item['name']]
        if project_id == DEFAULT_PROJECT:
            known = {item['id'] for item in images}
            for path in sorted(self.legacy.glob('*.png')):
                if path.stem in known or not re.fullmatch(IMAGE_ID, path.stem):
                    continue
                try:
                    with path.open('rb') as file:
                        width, height = png_size(file.read(33))
                except (OSError, ValueError):
                    continue
                images.append({'id': path.stem, 'name': f'既有基准图 · {path.stem[:8]}',
                               'width': width, 'height': height, 'source': 'legacy'})
        return images

    def save(self, project_id, name, source, data, region=None):
        self.require_project(project_id)
        name = name.strip()
        if not name:
            raise ValueError('请输入图片名称')
        width, height = png_size(data)
        if region is not None:
            if (set(region) != {'left', 'top', 'right', 'bottom'} or
                any(type(value) is not int or value < 0 or value > 32767 for value in region.values()) or
                region['right'] - region['left'] != width or region['bottom'] - region['top'] != height):
                raise ValueError('图片坐标与截图尺寸不一致')
        try:
            with Image.open(io.BytesIO(data), formats=['PNG']) as image:
                image.verify()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValueError('PNG 图片无法读取') from exc
        region_json = json.dumps(region, sort_keys=True) if region is not None else None
        digest = hashlib.sha256(f'{source}\0{name}\0{region_json}\0'.encode())
        digest.update(data)
        replaced = []
        temps = []
        try:
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if not conn.execute('SELECT 1 FROM ui_projects WHERE id=?', (project_id,)).fetchone():
                    raise ValueError('项目不存在')
                rows = conn.execute(
                    'SELECT id,region FROM ui_images WHERE project_id=? AND name=? ORDER BY hidden ASC, rowid DESC',
                    (project_id, name)).fetchall()
                image_id = rows[0]['id'] if rows else digest.hexdigest()
                old_regions = {row['id']: json.loads(row['region']) if row['region'] else None for row in rows}
                ids = list(old_regions) or [image_id]
                for key in ids:
                    path = self.path(key, project_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    original = path.read_bytes() if path.is_file() else None
                    temp = path.with_name(f'{uuid.uuid4().hex}.tmp')
                    temps.append(temp)
                    temp.write_bytes(data)
                    temp.replace(path)
                    replaced.append((path, original))
                if rows:
                    conn.execute('''UPDATE ui_images SET width=?,height=?,source=?,region=?,hidden=(id<>?)
                        WHERE project_id=? AND name=?''',
                        (width, height, source, region_json, image_id, project_id, name))
                    if region is not None:
                        for row in conn.execute('SELECT id,body FROM cases').fetchall():
                            case = json.loads(row['body'])
                            changed = False
                            for step in case.get('steps', []):
                                options = (step.get('condition') or step).get('options') or {}
                                baseline_id = options.get('baselineId')
                                if (options.get('baselineProjectId') == project_id and baseline_id in old_regions
                                    and options.get('mode') == 'roi' and old_regions[baseline_id] is not None
                                    and old_regions[baseline_id] != region
                                    and options.get('roi') == old_regions[baseline_id]):
                                    options['roi'] = region
                                    changed = True
                            if changed:
                                case['version'] = case.get('version', 1) + 1
                                conn.execute('UPDATE cases SET body=? WHERE id=?',
                                             (json.dumps(case, ensure_ascii=False), row['id']))
                else:
                    conn.execute('''INSERT INTO ui_images (project_id,id,name,width,height,source,region)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (project_id, image_id, name, width, height, source, region_json))
        except Exception:
            for path, original in reversed(replaced):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    temp = path.with_name(f'{uuid.uuid4().hex}.tmp')
                    try:
                        temp.write_bytes(original)
                        temp.replace(path)
                    finally:
                        temp.unlink(missing_ok=True)
            raise
        finally:
            for temp in temps:
                temp.unlink(missing_ok=True)
        return {'id': image_id, 'name': name, 'width': width, 'height': height,
                'source': source, 'region': region, 'aliases': ids[1:] if rows else []}
