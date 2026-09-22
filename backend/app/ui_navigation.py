"""Runner navigation knowledge: device-scoped, separate from the user-maintained tree."""
import json
import sqlite3
from pathlib import Path

from .ui_map import MapStore, resolve


class NavigationStore:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / 'navigation.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE IF NOT EXISTS knowledge(serial TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def save(self, serial, knowledge):
        # Worker messages are bounded; no screenshot, input value or coordinate is stored.
        if not isinstance(knowledge, dict):
            return
        edges, goals = knowledge.get('edges'), knowledge.get('goals')
        if not isinstance(edges, list) or not isinstance(goals, list) or len(edges) > 500 or len(goals) > 200:
            return
        body = json.dumps({'edges': edges, 'goals': goals}, ensure_ascii=False)
        if len(body.encode()) > 700_000:
            return
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT OR REPLACE INTO knowledge VALUES (?, ?)', (serial, body))

    def load(self, serial):
        with sqlite3.connect(self.db) as c:
            row = c.execute('SELECT body FROM knowledge WHERE serial=?', (serial,)).fetchone()
        knowledge = json.loads(row[0]) if row else {'edges': [], 'goals': []}
        seeds = {'pages': [], 'edges': []}
        maps = MapStore(self.root)
        graph = maps.graph(serial)
        observations = {}
        # UI labels and source trees bootstrap navigation without a manual recording.
        for page in graph['pages'][:500]:
            try:
                obs = maps.observation(serial, page['observationId'])
                observations[page['id']] = obs
                seeds['pages'].append({'id': page['id'], 'name': page['name'], 'nodes': obs['nodes']})
            except (KeyError, ValueError):
                continue
        for edge in graph['edges'][:1000]:
            if edge.get('status') not in ('observed', 'verified') or not edge.get('locator'):
                continue
            if edge['from'] not in observations or edge['to'] not in observations:
                continue
            try:
                node = resolve(observations[edge['from']], edge['locator'])
            except (KeyError, ValueError):
                continue
            # Export attribute selectors only when independently unique without XPath.
            selector = {k: node[k] for k in ('package', 'className', 'resourceId', 'label')}
            matches = [n for n in observations[edge['from']]['nodes'] if all(not v or n[k] == v for k, v in selector.items())]
            if len(matches) == 1 and node['clickable'] and not node['checkable']:
                seeds['edges'].append({'from': edge['from'], 'to': edge['to'], 'selector': selector})
        return {'knowledge': knowledge, 'seeds': seeds}
