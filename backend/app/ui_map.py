"""Persistent device workbench. Conservative page matching; no Android navigation keys."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import re
import sqlite3
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/ui/map', tags=['page-map'])


def uid():
    return uuid.uuid4().hex


def parse_tree(xml: str):
    if len(xml) > 8_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:
        raise ValueError('XML 超限或包含不支持的定义')
    root = ET.fromstring(xml)
    if root.tag != 'hierarchy':
        raise ValueError('不是 Android hierarchy')
    nodes = []
    def visit(raw, parent, path, depth):
        if depth > 80 or len(nodes) > 5000:
            raise ValueError('控件树过深或过大')
        a = raw.attrib
        bounds = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', a.get('bounds', ''))
        n = dict(index=len(nodes), parent=parent, xpath=path, resourceId=a.get('resource-id', ''),
                 text='' if a.get('password') == 'true' else a.get('text', ''),
                 description='' if a.get('password') == 'true' else a.get('content-desc', ''),
                 className=a.get('class', ''), package=a.get('package', ''),
                 bounds=list(map(int, bounds.groups())) if bounds else None,
                 clickable=a.get('clickable') == 'true', enabled=a.get('enabled') == 'true',
                 checkable=a.get('checkable') == 'true', checked=a.get('checked') == 'true',
                 scrollable=a.get('scrollable') == 'true', selected=a.get('selected') == 'true', children=[])
        nodes.append(n)
        for i, child in enumerate(raw.findall('node')):
            n['children'].append(visit(child, n['index'], f'{path}/node[{i+1}]', depth+1))
        n['label'] = n['text'] or n['description'] or next((nodes[i]['label'] for i in n['children'] if nodes[i]['label']), '')
        return n['index']
    for i, raw in enumerate(root.findall('node')):
        visit(raw, None, f'/hierarchy/node[{i+1}]', 0)
    # Exact conservative signature: dynamic text may produce a new candidate, never guessed identity.
    content = nodes
    signature = hashlib.sha256(json.dumps([(n['parent'], n['package'], n['className'], n['resourceId'],
        '' if n['package'] == 'com.android.systemui' or n['resourceId'].endswith('/summary') or 'EditText' in n['className'] else n['text'],
        '' if n['package'] == 'com.android.systemui' else n['description'], n['selected']) for n in content], ensure_ascii=False).encode()).hexdigest()
    geometry = hashlib.sha256(json.dumps([(n['bounds'], n['enabled']) for n in nodes]).encode()).hexdigest()
    return {'nodes': nodes, 'signature': signature, 'geometry': geometry, 'rotation': root.get('rotation', '0')}


def identity(obs):
    """Keep structure, titles and selected tabs; omit value/summary fields, not arbitrary text."""
    def text(n, key):
        value = n.get(key, '')
        rid = n['resourceId'].rsplit('/', 1)[-1].lower()
        if n['package'] == 'com.android.systemui' or rid in ('summary', 'widget_summary') or 'EditText' in n['className']:
            return ''
        if re.fullmatch(r'\s*\d+(?:[.,]\d+)?\s*(?:%|MB|GB|TB|分钟|分钟前|小时|秒|seconds?|minutes?|hours?)\s*', value, re.I):
            return '<value>'
        return value
    fields = [(n['parent'], n['package'], n['className'], n['resourceId'], text(n, 'text'), text(n, 'description'), n.get('selected', False)) for n in obs['nodes']]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def observation_package(obs):
    # Multiple application windows are ambiguous; do not guess a launch target.
    packages = {n['package'] for n in obs['nodes'] if n['package'] and n['package'] not in ('android', 'com.android.systemui')}
    return next(iter(packages)) if len(packages) == 1 else None


def locator(tree, node):
    def matches(fields):
        return [n for n in tree['nodes'] if all(n[k] == v for k, v in fields.items())]
    for fields in ({'package': node['package'], 'resourceId': node['resourceId']},
                   {k: node[k] for k in ('package', 'resourceId', 'className', 'text', 'description', 'label')}):
        if (fields.get('resourceId') or len(fields) > 2) and len(matches(fields)) == 1:
            return {'fields': fields, 'xpath': node['xpath'], 'strategy': 'attributes'}
    return {'fields': {k: node[k] for k in ('package', 'resourceId', 'className', 'text', 'description', 'label')},
            'xpath': node['xpath'], 'strategy': 'xpath', 'fragile': True}


def resolve(tree, loc):
    candidates = [n for n in tree['nodes'] if all(n[k] == v for k, v in loc['fields'].items())
                  and (loc['strategy'] != 'xpath' or n['xpath'] == loc['xpath'])]
    if len(candidates) != 1:
        raise ValueError(f'定位命中 {len(candidates)} 个元素，已停止')
    n = candidates[0]
    b = n['bounds']
    if not n['enabled'] or not b or b[2] <= b[0] or b[3] <= b[1]:
        raise ValueError('元素禁用或不在可操作区域')
    return n


def route(graph, source, target, allow_observed=False):
    queue = deque([(source, [])]); seen = set()
    while queue:
        page, edges = queue.popleft()
        if page == target:
            return edges
        if page in seen:
            continue
        seen.add(page)
        for e in graph['edges']:
            if e['from'] == page and e['status'] in (('verified', 'observed') if allow_observed else ('verified',)):
                queue.append((e['to'], edges + [e]))
    names = {p['id']: p['name'] for p in graph.get('pages', [])}
    raise ValueError(f"缺少从「{names.get(source, source)}」到「{names.get(target, target)}」的已记录路径。请从已保存父页面读取快照，进入目标页后切回选择元素并保存页面入口。")


class Device:
    def __init__(self, serial):
        self.serial = serial

    async def command(self, *args):
        proc = await asyncio.create_subprocess_exec('adb', '-s', self.serial, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 20)
        except (TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise ValueError(err.decode(errors='replace')[:500] or 'ADB 命令失败')
        return out

    async def tree(self):
        path = '/data/local/tmp/ui-map-' + uid() + '.xml'
        try:
            await self.command('shell', 'uiautomator', 'dump', path)
            return parse_tree((await self.command('exec-out', 'cat', path)).decode())
        finally:
            try:
                await self.command('shell', 'rm', '-f', path)
            except Exception:
                pass

    async def frame(self):
        png = await self.command('exec-out', 'screencap', '-p')
        if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) < 24:
            raise ValueError('设备没有返回 PNG 截图')
        w, h = struct.unpack('>II', png[16:24])
        return {'image': 'data:image/png;base64,' + base64.b64encode(png).decode(), 'width': w, 'height': h}

    async def observe(self):
        start = time.time()
        for _ in range(2):
            before = await self.tree()
            frame = await self.frame()
            after = await self.tree()
            if (before['signature'], before['geometry'], before['rotation']) == (after['signature'], after['geometry'], after['rotation']):
                return {**after, **frame, 'identity': identity(after), 'capturedAt': time.time(), 'captureStartedAt': start,
                        'id': uid(), 'serial': self.serial, 'displayId': 0}
        raise ValueError('截图期间布局发生变化，请等待页面稳定后刷新')

    async def tap(self, node, width, height):
        x1, y1, x2, y2 = node['bounds']
        x, y = (x1+x2)//2, (y1+y2)//2
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError('元素超出当前屏幕范围')
        await self.command('shell', 'input', 'tap', str(x), str(y))

    async def launch(self, package):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*', package):
            raise ValueError('应用包名无效，未启动应用')
        output = (await self.command('shell', 'cmd', 'package', 'resolve-activity', '--brief',
                    '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', package)).decode(errors='replace')
        components = [line.strip() for line in output.splitlines()
                      if re.fullmatch(re.escape(package) + r'/[A-Za-z0-9_.$]+', line.strip())]
        if len(components) != 1:
            raise ValueError(f'应用 {package} 没有可用的标准启动入口，请手动进入应用')
        result = (await self.command('shell', 'am', 'start', '-W', '-a', 'android.intent.action.MAIN',
                    '-c', 'android.intent.category.LAUNCHER', '-n', components[0])).decode(errors='replace')
        if re.search(r'Error:|Exception|Permission Denial', result, re.I):
            raise ValueError('应用启动失败：' + result[:400])

    async def swipe(self, gesture, width, height):
        w, h = width - 1, height - 1
        await self.command('shell', 'input', 'swipe', str(round(gesture['startX']*w)), str(round(gesture['startY']*h)),
                           str(round(gesture['endX']*w)), str(round(gesture['endY']*h)), '450')


class MapStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / 'maps.db'
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS maps(serial TEXT PRIMARY KEY, body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, serial TEXT, body TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, serial TEXT, body TEXT)')

    def connect(self):
        return sqlite3.connect(self.db)

    def graph(self, serial):
        with self.connect() as c:
            row = c.execute('SELECT body FROM maps WHERE serial=?', (serial,)).fetchone()
        g = json.loads(row[0]) if row else {'revision': 0, 'pages': [], 'elements': [], 'edges': [], 'deleted': []}
        if g.get('schemaVersion') != 2:
            self.migrate(serial, g)
        if g.get('entryVersion') != 1:
            self.migrate_entries(serial, g)
        return g

    def migrate(self, serial, g):
        backup = self.db.with_name('maps-before-tree-v2.db')
        if not backup.exists():
            with self.connect() as source, sqlite3.connect(backup) as target:
                source.backup(target)
        g['apps'] = []; g['schemaVersion'] = 2
        by_signature = {}
        needed = {p['signature'] for p in g['pages']}
        with self.connect() as c:
            for row in c.execute('SELECT body FROM evidence WHERE serial=? ORDER BY rowid', (serial,)):
                obs = json.loads(row[0])
                if obs['signature'] in needed: by_signature[obs['signature']] = obs
        for p in g['pages']:
            obs = by_signature.get(p['signature'])
            package = p.get('package') or (observation_package(obs) if obs else None)
            app = next((a for a in g['apps'] if a['package'] == package), None)
            if not app:
                app = {'id': uid(), 'package': package, 'name': package or '待确认应用'}; g['apps'].append(app)
            p['appId'] = app['id']; p['package'] = package
            if obs:
                p['identity'] = identity(obs); p['observationId'] = obs['id']
            edge = next((e for e in g['edges'] if e['id'] == p.get('parentEdgeId')), None)
            p['parentPageId'] = edge['from'] if edge else None
            p['navigationStatus'] = 'recorded' if edge else 'app-entry'
        # Recover only failures with historical proof that the actual destination has the same stable identity.
        with self.connect() as c:
            failures = [json.loads(r[0]) for r in c.execute('SELECT body FROM executions WHERE serial=?', (serial,))]
        for e in g['edges']:
            if e['status'] != 'stale': continue
            target = next((p for p in g['pages'] if p['id'] == e['to']), None)
            for run in failures:
                if '入口未到达目标页' not in run.get('message', ''): continue
                try: actual = self.observation(serial, run['lastObservationId'])
                except (ValueError, KeyError): continue
                if target and target.get('identity') == identity(actual):
                    e['status'] = 'observed'; e['recoveryReason'] = '历史到达快照仅动态摘要变化，待回放复验'; break
        self.put(serial, g)

    def migrate_entries(self, serial, g):
        # Backfill only provable source controls. App-launch and swipe entries have no control XPath.
        pending = []
        for page in g['pages']:
            edge = next((e for e in g['edges'] if e['id'] == page.get('parentEdgeId')), None)
            source = next((p for p in g['pages'] if edge and p['id'] == edge['from']), None)
            if edge and edge.get('locator') and source: pending.append((page, edge, source))
        if pending:
            backup = self.db.with_name('maps-before-parent-elements.db')
            if not backup.exists():
                with self.connect() as source_db, sqlite3.connect(backup) as target_db: source_db.backup(target_db)
            with self.connect() as c:
                for row in c.execute('SELECT body FROM evidence WHERE serial=? ORDER BY rowid DESC', (serial,)):
                    obs = json.loads(row[0]); stable = identity(obs)
                    for page, edge, source in pending[:]:
                        if (source.get('identity') != stable if source.get('identity') else source['signature'] != obs['signature']): continue
                        try: node = resolve(obs, edge['locator'])
                        except ValueError: continue
                        page['entry'] = {'sourcePageId': source['id'], 'observationId': obs['id'], 'nodeIndex': node['index'], 'locator': edge['locator']}
                        match = next((e for e in g['elements'] if e['pageId'] == source['id'] and e['locator'] == edge['locator']), None)
                        if match: page['entry']['elementId'] = match['id']
                        pending.remove((page, edge, source))
                    if not pending: break
        g['entryVersion'] = 1
        self.put(serial, g)

    def put(self, serial, graph):
        graph['revision'] += 1
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO maps VALUES (?,?)', (serial, json.dumps(graph, ensure_ascii=False)))

    def remember(self, obs):
        with self.connect() as c:
            c.execute('INSERT INTO evidence VALUES (?,?,?)', (obs['id'], obs['serial'], json.dumps(obs)))

    def observation(self, serial, key):
        with self.connect() as c:
            row = c.execute('SELECT body FROM evidence WHERE id=? AND serial=?', (key, serial)).fetchone()
        if not row:
            raise ValueError('检查快照不存在，请重新读取')
        return json.loads(row[0])

    def page(self, graph, obs):
        candidates = [p for p in graph['pages'] if p.get('identity', p['signature']) == (identity(obs) if p.get('identity') else obs['signature'])]
        if len(candidates) > 1:
            raise ValueError('多个页面匹配，需修复页面身份')
        return candidates[0] if candidates else None

    def page_package(self, graph, page_id, serial):
        page = next(p for p in graph['pages'] if p['id'] == page_id)
        if page.get('package'):
            return page['package']
        # Upgrade old saved maps only from exact page evidence, not icon labels.
        package = None
        with self.connect() as c:
            rows = c.execute('SELECT body FROM evidence WHERE serial=? ORDER BY rowid DESC', (serial,))
            for row in rows:
                obs = json.loads(row[0])
                if obs['signature'] == page['signature']:
                    package = observation_package(obs)
                    if package:
                        break
            rows.close()
        if package:
            page['package'] = package
            self.put(serial, graph)
        return package

    def ensure_page(self, graph, obs):
        page = self.page(graph, obs)
        if page:
            if not page.get("package"):
                page["package"] = observation_package(obs)
            return page
        label = next((n['text'] for n in obs['nodes'] if n['text'] and n['package'] != 'com.android.systemui'), '未命名页面')
        page = {'id': uid(), 'name': label[:80], 'signature': obs['signature'], 'parentEdgeId': None, 'identity': identity(obs), 'observationId': obs['id'], 'package': observation_package(obs)}
        graph['pages'].append(page)
        return page


class Serial(BaseModel):
    serial: str = Field(pattern=r'^[a-zA-Z0-9._:-]{1,128}$')


class ElementRequest(Serial):
    observationId: str
    nodeIndex: int = Field(ge=0)
    name: str = Field(default='', max_length=160)
    pageId: str | None = None
    elementId: str | None = None
    appId: str | None = None


class TapRequest(ElementRequest):
    recordNavigation: bool = False


class SwipeRequest(Serial):
    observationId: str
    startX: float = Field(ge=0, le=1)
    startY: float = Field(ge=0, le=1)
    endX: float = Field(ge=0, le=1)
    endY: float = Field(ge=0, le=1)


class ExecuteRequest(Serial):
    targetId: str
    intent: Literal['navigate', 'click', 'on', 'off'] = 'navigate'
    requestId: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')


class EditRequest(Serial):
    id: str
    action: Literal['rename', 'delete', 'restore']
    name: str = Field(default='', max_length=160)


def store(request):
    return request.app.state.ui_map


@asynccontextmanager
async def guard(request, serial):
    ui = request.app.state.ui
    lock = ui.device_lock(serial)
    if lock.locked():
        raise HTTPException(409, '设备正在操作，请稍后再试')
    async with lock:
        if ui.has_active_device(serial):
            raise HTTPException(409, '设备正在运行 UI 用例或扫描，请先停止该任务')
        try:
            yield
        except TimeoutError as exc:
            raise HTTPException(504, '设备响应超时；若已发送点击，请刷新确认结果，不要重复点击') from exc
        except (ValueError, OSError, ET.ParseError) as exc:
            raise HTTPException(409, str(exc)) from exc


def get_node(obs, index):
    if index >= len(obs['nodes']):
        raise ValueError('节点已失效')
    return obs['nodes'][index]


@router.post('/graph')
async def graph(value: Serial, request: Request):
    return store(request).graph(value.serial)


@router.post('/observe')
async def observe(value: Serial, request: Request):
    async with guard(request, value.serial):
        obs = await Device(value.serial).observe()
        store(request).remember(obs)
        return obs


@router.post('/frame')
async def frame(value: Serial, request: Request):
    async with guard(request, value.serial):
        return await Device(value.serial).frame()


@router.post('/save')
async def save(value: ElementRequest, request: Request):
    async with guard(request, value.serial):
        db = store(request); obs = db.observation(value.serial, value.observationId)
        node = get_node(obs, value.nodeIndex); g = db.graph(value.serial)
        p = next((p for p in g['pages'] if p['id'] == value.pageId), None)
        if not p and not value.pageId and value.appId:
            app = next((a for a in g['apps'] if a['id'] == value.appId), None)
            if not app or app['package'] != observation_package(obs): raise ValueError('当前元素不属于所选应用')
            p = db.page(g, obs)
            if p and p.get('parentPageId'): raise ValueError('当前画面属于已保存的父元素，请先选择该父元素')
            if not p:
                p = db.ensure_page(g, obs)
                p.update(appId=app['id'], parentPageId=None, navigationStatus='app-entry')
        if not p:
            raise ValueError('请先选择应用或父元素')
        if not p.get('appId') or not any(a['id'] == p['appId'] for a in g.get('apps', [])):
            raise ValueError('所属应用不存在')
        if p.get('identity') != identity(obs):
            raise ValueError('当前标定快照与所选页面不匹配，请保存为新页面或读取正确页面')
        loc = locator(obs, node)
        updating = next((e for e in g['elements'] if e['id'] == value.elementId), None)
        if value.elementId and (not updating or updating['pageId'] != p['id']):
            raise ValueError('要重新标定的元素不属于当前页面')
        match = updating or next((e for e in g['elements'] if e['pageId'] == p['id'] and e.get('locator') == loc), None)
        element = {'id': match['id'] if match else uid(), 'pageId': p['id'], 'name': value.name.strip() or node['label'] or node['resourceId'] or node['className'],
                   'locator': loc, 'observationId': obs['id'], 'nodeIndex': node['index'], 'checkable': node['checkable'], 'validation': 'verified'}
        if match:
            g['elements'].remove(match)
        g['elements'].append(element)
        db.put(value.serial, g)
        return g


@router.post('/saved-observation')
async def saved_observation(value: ElementRequest, request: Request):
    try:
        return store(request).observation(value.serial, value.observationId)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/tap')
async def tap(value: TapRequest, request: Request):
    async with guard(request, value.serial):
        db = store(request); dev = Device(value.serial)
        obs = db.observation(value.serial, value.observationId)
        loc = locator(obs, get_node(obs, value.nodeIndex))
        before = await dev.observe()
        if identity(before) != identity(obs) or before['rotation'] != obs['rotation']:
            raise ValueError('设备页面与检查快照不同，已阻止点击；请重新读取控件')
        n = resolve(before, loc)
        if not n['clickable']:
            raise ValueError('该节点不可点击，请从候选层级中选择可点击父节点')
        descendants = list(n['children'])
        for index in descendants:
            descendants.extend(before['nodes'][index]['children'])
        if value.recordNavigation and (n['checkable'] or any(before['nodes'][i]['checkable'] for i in descendants)):
            raise ValueError('开关不能作为导航入口，请关闭录制后操作')
        g = db.graph(value.serial)
        db.remember(before)
        run_id = uid()
        with db.connect() as c:
            c.execute('INSERT INTO executions VALUES (?,?,?)', (run_id, value.serial, json.dumps({'state': 'outcome_unknown', 'before': before['id'], 'locator': loc})))
        await dev.tap(n, before['width'], before['height'])
        await asyncio.sleep(.3)
        after = await dev.observe(); db.remember(after)
        if value.recordNavigation and not n['checkable'] and not any(before['nodes'][i]['checkable'] for i in descendants):
            if before['signature'] != after['signature']:
                a = db.ensure_page(g, before); b = db.ensure_page(g, after)
                e = next((e for e in g['edges'] if e['from'] == a['id'] and e['to'] == b['id'] and e.get('locator') == loc), None)
                if not e:
                    e = {'id': uid(), 'from': a['id'], 'to': b['id'], 'locator': loc,
                         'name': value.name.strip() or n['label'] or '页面入口', 'status': 'observed'}
                    g['edges'].append(e)
                    # Only attach newly discovered destinations: no tree cycles.
                    if not b['parentEdgeId'] and not any(x['from'] == b['id'] for x in g['edges']):
                        b['parentEdgeId'] = e['id']
                else:
                    e['status'] = 'verified'
                db.put(value.serial, g)
        with db.connect() as c:
            c.execute('UPDATE executions SET body=? WHERE id=?', (json.dumps({'state': 'action_sent', 'before': before['id'], 'after': after['id'], 'locator': loc}), run_id))
        return {'observation': after, 'graph': g, 'message': '点击已发送并刷新；页面归属和入口需在选择元素模式中显式保存。'}


@router.post('/edit')
async def edit(value: EditRequest, request: Request):
    async with guard(request, value.serial):
        db = store(request); g = db.graph(value.serial)
        for collection in ('pages', 'elements'):
            obj = next((x for x in g[collection] if x['id'] == value.id), None)
            if not obj:
                continue
            if value.action == 'rename':
                if not value.name.strip():
                    raise ValueError('名称不能为空')
                obj['name'] = value.name.strip()
            elif value.action == 'delete':
                removed_elements = [e for e in g['elements'] if collection == 'pages' and (e['pageId'] == obj['id'] or e['id'] == obj.get('entry', {}).get('elementId'))]
                removed_edges = [e for e in g['edges'] if collection == 'pages' and (e['from'] == obj['id'] or e['to'] == obj['id'])]
                g['elements'] = [e for e in g['elements'] if e not in removed_elements]
                g['edges'] = [e for e in g['edges'] if e not in removed_edges]
                g[collection].remove(obj)
                g['deleted'].append({'collection': collection, 'object': obj, 'elements': removed_elements, 'edges': removed_edges})
            else:
                raise ValueError('此操作不适用于对象')
            db.put(value.serial, g); return g
        if value.action == 'restore':
            obj = next((x for x in g['deleted'] if x['object']['id'] == value.id), None)
            if obj:
                page_ids = {p['id'] for p in g['pages']} | ({obj['object']['id']} if obj['collection'] == 'pages' else set())
                if obj['collection'] == 'elements' and obj['object']['pageId'] not in page_ids:
                    raise ValueError('所属页面已删除，请先恢复页面')
                g[obj['collection']].append(obj['object'])
                g['elements'].extend(obj.get('elements', []))
                g['edges'].extend(e for e in obj.get('edges', []) if e['from'] in page_ids and e['to'] in page_ids)
                g['deleted'].remove(obj)
                db.put(value.serial, g); return g
        raise ValueError('对象不存在')


@router.post('/execute')
async def execute(value: ExecuteRequest, request: Request):
    async with guard(request, value.serial):
        db = store(request); dev = Device(value.serial); g = db.graph(value.serial)
        with db.connect() as c:
            previous = c.execute('SELECT body FROM executions WHERE id=?', (value.requestId,)).fetchone()
            if previous:
                raise ValueError('该请求已处理或结果待确认，请刷新查看设备，不能重复提交')
            c.execute('INSERT INTO executions VALUES (?,?,?)', (value.requestId, value.serial, json.dumps({'state': 'outcome_unknown', 'request': value.model_dump()})))
        logs = []; obs = None
        try:
            element = next((e for e in g['elements'] if e['id'] == value.targetId), None)
            target = element['pageId'] if element else value.targetId
            if not any(p['id'] == target for p in g['pages']):
                raise ValueError('目标页面不存在')
            obs = await dev.observe(); db.remember(obs)
            source = db.page(g, obs)
            try:
                if not source:
                    raise ValueError('当前页无法可靠识别')
                path = route(g, source['id'], target, allow_observed=True)
            except ValueError as missing_path:
                package = db.page_package(g, target, value.serial)
                if not package:
                    raise ValueError(str(missing_path) + '；目标页尚无可靠包名，请重新读取并保存目标页元素') from missing_path
                await dev.launch(package)
                logs.append('已启动应用：' + package)
                obs = await dev.observe(); db.remember(obs)
                source = db.page(g, obs)
                if not source:
                    raise ValueError(f'已启动 {package}，但落地页尚未记录；请读取画面并走一次应用内部路径')
                try:
                    path = route(g, source['id'], target, allow_observed=True)
                except ValueError as exc:
                    raise ValueError(f'已启动 {package}；' + str(exc)) from exc
            if len(path) > 12:
                raise ValueError('路径超过 12 步，请选择更近起点')
            for edge in path:
                source_page = next(p for p in g['pages'] if p['id'] == edge['from'])
                if (source_page.get('identity') != identity(obs) if source_page.get('identity') else obs['signature'] != source_page['signature']):
                    raise ValueError('导航来源页不匹配')
                if edge.get('action') == 'swipe':
                    await dev.swipe(edge['gesture'], obs['width'], obs['height'])
                else:
                    n = resolve(obs, edge['locator'])
                    if not n['clickable']:
                        raise ValueError('导航入口当前不可点击')
                    await dev.tap(n, obs['width'], obs['height'])
                await asyncio.sleep(.3)
                obs = await dev.observe(); db.remember(obs)
                found = db.page(g, obs)
                if not found or found['id'] != edge['to']:
                    edge['status'] = 'stale'; db.put(value.serial, g)
                    raise ValueError('入口未到达目标页，已标记路径失效；没有重复点击')
                if edge['status'] == 'observed':
                    edge['status'] = 'verified'; db.put(value.serial, g)
                logs.append('已到达：' + found['name'])
            if element and element.get('validation') == 'recapture' and value.intent != 'navigate':
                raise ValueError('跨应用移动后必须先在新页面重新标定，不能沿用旧定位')
            if element and element.get('validation') == 'pending':
                resolve(obs, element['locator'])
                element['validation'] = 'verified'; db.put(value.serial, g)
            if value.intent != 'navigate':
                if not element:
                    raise ValueError('请选择元素')
                n = resolve(obs, element['locator'])
                if not n['clickable']:
                    raise ValueError('元素不可点击，请保存可点击节点')
                desired = value.intent == 'on'
                if value.intent in ('on', 'off') and not n['checkable']:
                    raise ValueError('当前元素未暴露可读开关状态')
                if value.intent in ('on', 'off') and n['checked'] == desired:
                    logs.append('目标状态已满足：零点击')
                else:
                    await dev.tap(n, obs['width'], obs['height'])
                    await asyncio.sleep(.3)
                    obs = await dev.observe(); db.remember(obs)
                    if value.intent in ('on', 'off'):
                        if db.page(g, obs) is None or db.page(g, obs)['id'] != target or resolve(obs, element['locator'])['checked'] != desired:
                            raise ValueError('点击已发送，但不能确认目标状态；未重试')
                        logs.append('开关目标状态验证通过')
                    else:
                        logs.append('点击已发送；未配置业务断言')
            result = {'observation': obs, 'graph': g, 'message': '；'.join(logs) or '已在目标页面', 'state': 'action_sent' if value.intent == 'click' else 'succeeded'}
            with db.connect() as c:
                c.execute('UPDATE executions SET body=? WHERE id=?', (json.dumps({'state': result['state'], 'logs': logs, 'lastObservationId': obs['id']}), value.requestId))
            return result
        except Exception as exc:
            with db.connect() as c:
                c.execute('UPDATE executions SET body=? WHERE id=?', (json.dumps({'state': 'needs_review', 'message': str(exc), 'logs': logs, 'lastObservationId': obs['id'] if obs else None}), value.requestId))
            raise


@router.post('/swipe')
async def swipe(value: SwipeRequest, request: Request):
    async with guard(request, value.serial):
        db = store(request); dev = Device(value.serial)
        old = db.observation(value.serial, value.observationId)
        before = await dev.observe()
        if (old['signature'], old['geometry'], old['rotation']) != (before['signature'], before['geometry'], before['rotation']):
            raise ValueError('页面已变化，未发送滑动，请重新读取画面')
        gesture = {k: getattr(value, k) for k in ('startX', 'startY', 'endX', 'endY')}
        await dev.swipe(gesture, before['width'], before['height'])
        await asyncio.sleep(.3)
        after = await dev.observe(); db.remember(after)
        g = db.graph(value.serial)
        return {'observation': after, 'graph': g, 'message': '滑动已发送并刷新，尚未保存页面。'}


class AppSave(Serial):
    observationId: str
    name: str = Field(default='', max_length=160)


class PageSave(AppSave):
    entryElementId: str | None = None
    appId: str
    parentPageId: str | None = None
    draftToken: str | None = None
    allowMissing: bool = False


class MoveElement(Serial):
    elementId: str
    pageId: str | None = None
    undo: bool = False


@router.post('/save-app')
async def save_app(value: AppSave, request: Request):
    async with guard(request, value.serial):
        db = store(request); obs = db.observation(value.serial, value.observationId)
        package = observation_package(obs)
        if not package: raise ValueError('当前画面包含多个应用或系统弹窗，请先进入要保存的应用')
        g = db.graph(value.serial)
        app = next((a for a in g['apps'] if a['package'] == package), None)
        if not app:
            app = {'id': uid(), 'name': value.name.strip() or package, 'package': package}; g['apps'].append(app)
        elif value.name.strip(): app['name'] = value.name.strip()
        db.put(value.serial, g)
        return {'graph': g, 'appId': app['id']}


@router.post('/save-page')
async def save_page(value: PageSave, request: Request):
    async with guard(request, value.serial):
        db = store(request); obs = db.observation(value.serial, value.observationId); g = db.graph(value.serial)
        app = next((a for a in g['apps'] if a['id'] == value.appId), None)
        if not app or app['package'] != observation_package(obs):
            raise ValueError('当前快照不属于所选应用，请选择对应应用')
        parent = next((p for p in g['pages'] if p['id'] == value.parentPageId), None)
        if value.parentPageId and (not parent or (parent.get('appId') != app['id'] and not value.entryElementId)):
            raise ValueError('请选择同一应用下的父页面')
        existing = db.page(g, obs)
        if parent and existing:
            seen = {existing['id']}; cursor = parent
            while cursor:
                if cursor['id'] in seen: raise ValueError('页面不能移动到自己或自己的子页面下')
                seen.add(cursor['id']); cursor = next((p for p in g['pages'] if p['id'] == cursor.get('parentPageId')), None)
        entry_element = next((e for e in g['elements'] if e['id'] == value.entryElementId), None)
        if value.entryElementId:
            if not entry_element or not parent or entry_element['pageId'] != parent['id']:
                raise ValueError('父元素来源不匹配')
            if any(p.get('entry', {}).get('elementId') == entry_element['id'] and p['id'] != (existing or {}).get('id') for p in g['pages']):
                raise ValueError('该元素已绑定其他目标，不能重复绑定')
        entry_info = None
        if entry_element:
            source_obs = db.observation(value.serial, entry_element['observationId'])
            source_node = get_node(source_obs, entry_element['nodeIndex'])
            if not source_node['clickable'] or source_node['checkable']:
                raise ValueError('请选择可点击的导航入口；图标可在节点层级中切换到可点击父控件')
            entry_info = {'elementId': entry_element['id'], 'sourcePageId': parent['id'], 'observationId': source_obs['id'], 'nodeIndex': source_node['index'], 'locator': entry_element['locator']}
        edge_data = None
        reason = '没有可关联的进入动作'
        if parent:
            drafts = getattr(request.app.state, 'ui_drafts', {})
            draft = drafts.get(value.draftToken)
            if draft and draft['serial'] == value.serial and draft.get('complete') and time.time() - draft['createdAt'] <= 1800:
                if len(draft['steps']) == 1 and not draft.get('overflow') and draft.get('sourceObservationId'):
                    before = db.observation(value.serial, draft['sourceObservationId'])
                    source = db.page(g, before)
                    if source and source['id'] == parent['id']:
                        step = draft['steps'][0]
                        if step['action'] == 'tap':
                            x, y = step['startX'] * before['width'], step['startY'] * before['height']
                            hits = [n for n in before['nodes'] if n['clickable'] and n['enabled'] and n['bounds'] and n['bounds'][0] <= x < n['bounds'][2] and n['bounds'][1] <= y < n['bounds'][3]]
                            hits.sort(key=lambda n: (n['bounds'][2]-n['bounds'][0])*(n['bounds'][3]-n['bounds'][1]))
                            if hits:
                                n = hits[0]; descendants = list(n['children'])
                                for i in descendants: descendants.extend(before['nodes'][i]['children'])
                                if n['checkable'] or any(before['nodes'][i]['checkable'] for i in descendants):
                                    raise ValueError('这次动作是开关操作，不能作为页面入口保存')
                                if entry_element and resolve(before, entry_element['locator'])['index'] != n['index']:
                                    raise ValueError('实际进入动作与选定父元素不同，请选择实际点击的元素')
                                loc = locator(before, n)
                                edge_data = {'locator': loc, 'name': n['label'] or '页面入口'}
                                matched = entry_element or next((e for e in g['elements'] if e['pageId'] == source['id'] and e['locator'] == loc), None)
                                entry_info = {'sourcePageId': source['id'], 'observationId': before['id'], 'nodeIndex': n['index'], 'locator': loc}
                                if matched: entry_info['elementId'] = matched['id']
                        else:
                            if entry_element: raise ValueError('元素打开动作不能绑定为滑动，请点击所选元素进入')
                            edge_data = {'action': 'swipe', 'gesture': {k: step[k] for k in ('startX','startY','endX','endY')}, 'name': '滑动入口'}
                else: reason = '这次包含多步操作；请从已保存父页面读取快照，单次进入后保存，或先保存为入口待补充'
            if not edge_data and not value.allowMissing:
                # Updating an existing page need not destroy its recorded entry.
                if not existing or existing.get('parentPageId') != parent['id'] or not existing.get('parentEdgeId'):
                    raise ValueError(reason)
        page = existing or db.ensure_page(g, obs)
        page.update(appId=app['id'], package=app['package'], identity=identity(obs), observationId=obs['id'])
        if value.name.strip(): page['name'] = value.name.strip()
        elif entry_element: page['name'] = entry_element['name']
        if parent:
            if page.get('parentPageId') != parent['id'] and page.get('parentEdgeId'):
                g['edges'] = [e for e in g['edges'] if e['id'] != page['parentEdgeId']]
                page['parentEdgeId'] = None
            page['parentPageId'] = parent['id'] if parent.get('appId') == app['id'] else None
            if edge_data:
                old = next((e for e in g['edges'] if e['id'] == page.get('parentEdgeId') and e['from'] == parent['id']), None)
                edge = {'id': old['id'] if old else uid(), 'from': parent['id'], 'to': page['id'], 'status': 'observed', **edge_data}
                if old: g['edges'].remove(old)
                g['edges'].append(edge); page['parentEdgeId'] = edge['id']; page['navigationStatus'] = 'recorded'
                if entry_info: page['entry'] = entry_info
                else: page.pop('entry', None)
            elif not page.get('parentEdgeId') or not any(e['id'] == page['parentEdgeId'] and e['from'] == parent['id'] for e in g['edges']):
                page['parentEdgeId'] = None; page['navigationStatus'] = 'missing'; page.pop('entry', None)
        else:
            page['parentPageId'] = None; page['parentEdgeId'] = None; page['navigationStatus'] = 'app-entry'
        if entry_info: page['entry'] = entry_info
        db.put(value.serial, g)
        return {'graph': g, 'pageId': page['id']}


@router.post('/move-element')
async def move_element(value: MoveElement, request: Request):
    async with guard(request, value.serial):
        db = store(request); g = db.graph(value.serial)
        element = next((e for e in g['elements'] if e['id'] == value.elementId), None)
        if not element: raise ValueError('元素不存在')
        if any(p.get('entry', {}).get('elementId') == element['id'] for p in g['pages']):
            raise ValueError('此元素已有子元素，请先更换父元素入口，不能仅移动它的叶子记录')
        if value.undo:
            old = element.get('moveUndo')
            if not old or not any(p['id'] == old['pageId'] for p in g['pages']): raise ValueError('原页面不存在，无法撤销移动')
            element.update(old); element.pop('moveUndo', None)
        else:
            page = next((p for p in g['pages'] if p['id'] == value.pageId), None)
            source = next((p for p in g['pages'] if p['id'] == element['pageId']), None)
            if not page or not source: raise ValueError('页面不存在')
            if page['id'] == source['id']: return g
            element['moveUndo'] = {'pageId': element['pageId'], 'validation': element.get('validation','verified')}
            element['pageId'] = page['id']
            element['validation'] = 'pending' if page.get('package') == source.get('package') else 'recapture'
        db.put(value.serial, g)
        return g


class ParentRead(Serial):
    pageId: str


@router.post('/parent-element')
async def parent_element(value: ParentRead, request: Request):
    db = store(request); g = db.graph(value.serial)
    page = next((p for p in g['pages'] if p['id'] == value.pageId), None)
    if not page: raise HTTPException(404, '父元素不存在')
    entry = page.get('entry')
    if not entry:
        return {'entry': None, 'message': '此节点尚无控件入口信息（应用启动、滑动或旧数据入口缺失），请补充入口元素'}
    obs = db.observation(value.serial, entry['observationId'])
    return {'entry': entry, 'observation': obs, 'node': get_node(obs, entry['nodeIndex']),
            'sourceName': next((p['name'] for p in g['pages'] if p['id'] == entry['sourcePageId']), '来源画面'),
            'targetName': page['name']}
