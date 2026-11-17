"""Runner navigation knowledge: device-scoped, separate from the user-maintained tree."""
import json
import sqlite3
from pathlib import Path

from .ui_map import MapStore, application_nodes, is_navigation_entry, label_tree_nodes, resolve


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
        normalized_nodes = {}
        # UI labels and source trees bootstrap navigation without a manual recording.
        for page in graph['pages'][:500]:
            try:
                obs = maps.observation(serial, page['observationId'])
                nodes = application_nodes(obs)
                if not nodes:
                    continue
                observations[page['id']] = obs
                seeds['pages'].append({'id': page['id'], 'name': page['name'], 'nodes': nodes})
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
            source = observations[edge['from']]
            if not is_navigation_entry(source, node):
                continue
            if edge['from'] not in normalized_nodes:
                normalized = [item.copy() for item in source['nodes']]
                label_tree_nodes(normalized)
                normalized_nodes[edge['from']] = {
                    item['index']: normalized[item['index']] for item in application_nodes(source)}
            normalized = normalized_nodes[edge['from']]
            current = normalized.get(node['index'])
            if current is None:
                continue
            fields = ('package', 'className', 'resourceId')
            selector = {key: current[key] for key in fields}
            matches = [n for n in normalized.values() if all(not v or n[k] == v for k, v in selector.items())]
            if len(matches) != 1 or not selector['resourceId']:
                selector['label'] = current['label']
                matches = [n for n in matches if not current['label'] or n['label'] == current['label']]
            if len(matches) == 1 and any(selector.values()):
                seeds['edges'].append({'from': edge['from'], 'to': edge['to'], 'selector': selector})
        return {'knowledge': knowledge, 'seeds': seeds}
