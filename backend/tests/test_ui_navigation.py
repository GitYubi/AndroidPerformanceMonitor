from app.ui_navigation import NavigationStore
from app.ui_map import MapStore


def test_navigation_is_device_scoped_and_does_not_edit_tree(tmp_path):
    store = NavigationStore(tmp_path)
    maps = MapStore(tmp_path)
    original = maps.graph('one')
    knowledge = {'edges': [{'from': 'a', 'to': 'b', 'selector': {'label': '显示'}}], 'goals': []}
    store.save('one', knowledge)
    assert store.load('one')['knowledge'] == knowledge
    assert store.load('two')['knowledge']['edges'] == []
    assert maps.graph('one') == original


def test_oversized_or_malformed_update_preserves_knowledge(tmp_path):
    store = NavigationStore(tmp_path)
    valid = {'edges': [], 'goals': [{'prompt': '进入显示页面'}]}
    store.save('one', valid)
    for invalid in (None, {}, {'edges': 'bad', 'goals': []}, {'edges': [{}] * 501, 'goals': []}):
        store.save('one', invalid)
    assert store.load('one')['knowledge'] == valid
