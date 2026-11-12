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
            images = [dict(row) for row in conn.execute(
                'SELECT id,name,width,height,source,region FROM ui_images WHERE project_id=? ORDER BY rowid DESC',
                (project_id,))]
        for item in images:
            item['region'] = json.loads(item['region']) if item['region'] else None
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
        image_id = digest.hexdigest()
        path = self.path(image_id, project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temp = path.with_name(f'{uuid.uuid4().hex}.tmp')
            try:
                temp.write_bytes(data)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        with self.connect() as conn:
            conn.execute('''INSERT OR IGNORE INTO ui_images (project_id,id,name,width,height,source,region) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                         (project_id, image_id, name, width, height, source, region_json))
        return {'id': image_id, 'name': name, 'width': width, 'height': height, 'source': source, 'region': region}
