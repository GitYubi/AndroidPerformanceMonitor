import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import ui_map as m
from app.ui_hierarchy_capture import default_display_xml, default_focus, default_physical_display


def tree(label='Page A', checked=False, duplicate=False):
    node=f'<node package="demo" class="android.widget.Switch" resource-id="demo:id/switch" text="Toggle" clickable="true" enabled="true" checkable="true" checked="{str(checked).lower()}" bounds="[10,20][90,60]" />'
    xml=f'<hierarchy rotation="0"><node package="demo" class="android.widget.LinearLayout" text="{label}" enabled="true" bounds="[0,0][100,100]">{node}{node if duplicate else ""}</node></hierarchy>'
    return {**m.parse_tree(xml),'id':m.uid(),'serial':'dev','width':100,'height':100,'image':'','capturedAt':1}


def setup(tmp_path, monkeypatch, observations):
    store=m.MapStore(tmp_path)
    lock=asyncio.Lock()
    req=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui_map=store,ui=SimpleNamespace(device_lock=lambda s:lock,has_active_device=lambda s:False))))
    class Fake:
        taps=[]
        swipes=[]
        launches=[]
        def __init__(self, serial): pass
        async def observe(self):
            return {**(observations.pop(0) if len(observations)>1 else observations[0]), 'id': m.uid()}
        async def tap(self,*args): self.taps.append(args)
        async def swipe(self,*args): self.swipes.append(args)
        async def launch(self,package): self.launches.append(package)
    monkeypatch.setattr(m,'Device',Fake)
    return store,req,Fake


async def save_app_page(req, obs, name='Page'):
    app=await m.save_app(m.AppSave(serial='dev',observationId=obs['id'],name='Demo'),req)
    page=await m.save_page(m.PageSave(serial='dev',observationId=obs['id'],appId=app['appId'],name=name),req)
    return page['pageId']


def test_switch_state_not_page_identity_and_xpath_unique():
    a,b=tree(),tree(checked=True)
    assert a['signature']==b['signature']
    assert tree('Page B')['signature']!=a['signature']
    dup=tree(duplicate=True)
    loc=m.locator(dup,dup['nodes'][2])
    assert loc['strategy']=='xpath'
    assert m.resolve(dup,loc)['index']==2
    bad={**loc,'strategy':'attributes'}
    with pytest.raises(ValueError,match='2 个'):m.resolve(dup,bad)


def test_multi_window_projection_preserves_legacy_page_and_selector_paths():
    legacy = tree(duplicate=True)
    app = '<node package="demo" class="android.widget.LinearLayout" text="Page A" enabled="true" bounds="[0,0][100,100]">'
    app += '<node package="demo" class="android.widget.Switch" resource-id="demo:id/switch" text="Toggle" enabled="true" bounds="[10,20][90,60]"/>' * 2
    app += '</node>'
    bar = '<node package="com.android.systemui" bounds="[0,0][100,10]" text="TIME"/>'
    other_display = '<node package="external" display-id="2"/>'

    def multi(time):
        xml = f'<hierarchy>{other_display}{bar.replace("TIME", time)}{app}</hierarchy>'
        return {**m.parse_tree(default_display_xml(xml, {0}).decode()), 'treeSource': m.MULTI_WINDOW}

    observed = multi('09:40')
    assert m.identity(observed) == m.identity(multi('09:41')) == m.identity(legacy)
    assert m.page_signature(observed) == m.page_signature(legacy)
    assert m.observation_package(observed) == 'demo'
    assert [n['package'] for n in observed['nodes']] == ['com.android.systemui', 'demo', 'demo', 'demo']
    old_loc = m.locator(legacy, legacy['nodes'][2])
    new_loc = m.locator(observed, observed['nodes'][3])
    assert old_loc['xpath'] == '/hierarchy/node[1]/node[2]'
    assert new_loc['xpath'] == '/hierarchy/node[2]/node[2]'
    assert m.resolve(legacy, old_loc)['index'] == 2
    assert m.resolve(observed, new_loc)['index'] == 3
    with pytest.raises(ValueError, match='来源'):
        m.resolve(observed, old_loc)
    with pytest.raises(ValueError, match='没有返回'):
        default_display_xml(f'<hierarchy>{other_display}</hierarchy>', {0, 2})
    with pytest.raises(ValueError, match='无法确认'):
        default_display_xml(f'<hierarchy>{bar}{other_display}</hierarchy>', {0, 2})


def test_vehicle_default_display_and_foreground_window_mapping():
    display_dump = """
  Display: mDisplayId=2 (organized)
  mFocusedApp=ActivityRecord{abc u0 demo/.Passenger t1}
  Display: mDisplayId=0 (organized)
  mCurrentFocus=Window{abc u0 vendor.systemui/Overlay}
  mFocusedApp=ActivityRecord{dec u0 demo/.MainActivity t2}
"""
    window_dump = """
  Window #3 Window{abc u0 demo/demo.MainActivity}:
    mDisplayId=2 taskId=3
    mAttrs={(0,0) ty=BASE_APPLICATION}
    Frames: parent=[0,0][100,100] frame=[0,0][90,90]
  Window #4 Window{def u0 demo/demo.MainActivity}:
    mDisplayId=0 taskId=4
    mAttrs={(0,0) ty=BASE_APPLICATION}
    Frames: parent=[0,0][100,100] frame=[0,0][100,100]
"""
    xml = """<hierarchy>
      <node package="demo" display-id="2" bounds="[0,0][90,90]"/>
      <node package="demo" display-id="0" bounds="[0,0][100,100]"/>
      <node package="demo" display-id="0" bounds="[0,70][100,100]"/>
      <node package="vendor.systemui" display-id="0" bounds="[0,0][100,10]"/>
      </hierarchy>"""
    filtered = default_display_xml(xml, {0, 2}, default_focus(display_dump), lambda: window_dump)
    observation = {**m.parse_tree(filtered.decode()), 'treeSource': m.MULTI_WINDOW}
    assert len(observation['nodes']) == 3
    assert m.observation_package(observation) == 'demo'
    assert [node['bounds'] for node in m.application_nodes(observation)] == [[0, 0, 100, 100]]

    def denied():
        raise PermissionError

    filtered = default_display_xml(xml, {0, 2}, default_focus(display_dump), denied)
    observation = {**m.parse_tree(filtered.decode()), 'treeSource': m.MULTI_WINDOW}
    assert len(observation['nodes']) == 3
    assert m.observation_package(observation) is None

    assert default_physical_display("""
  Display 2:
    mPrimaryDisplayDevice=HDMI(local:222)
  Display 0:
    mPrimaryDisplayDevice=主屏(local:4630947208271169569)
""") == '4630947208271169569'
    assert default_physical_display("""
  Display 0:
    mPrimaryDisplayDevice=Device
    mBaseDisplayInfo=DisplayInfo{"Device", displayId 0, uniqueId "local:123"}
""") == '123'


def test_frame_retries_multidisplay_warning_with_explicit_physical_mapping(monkeypatch):
    device = m.Device('vehicle')
    calls = []

    async def command(*args):
        calls.append(args)
        return b'[Warning] Multiple displays were found\n'

    async def read_default(*args):
        calls.append(args)
        return b'\x89PNG\r\n\x1a\n' + bytes(8) + bytes.fromhex('00000a00000005a0')

    monkeypatch.setattr(device, 'command', command)
    monkeypatch.setattr(device, 'run', read_default)
    frame = asyncio.run(device.frame())
    assert (frame['width'], frame['height']) == (2560, 1440)
    assert calls[0] == ('exec-out', 'screencap', '-p')
    assert calls[1][-2:] == ('vehicle', '--capture-default')


@pytest.mark.parametrize('adb_available', [False, True])
def test_inspect_restarts_capture_with_one_source_or_returns_screenshot(adb_available):
    calls = []

    class Device(m.Device):
        async def multi_window_tree(self):
            calls.append('multi')
            if calls.count('multi') > 1:
                raise ValueError('UIAutomator2 unavailable after screenshot')
            return tree()

        async def active_window_tree(self):
            calls.append('adb')
            return tree() if adb_available else m.parse_tree('<hierarchy/>')

        async def frame(self):
            calls.append('frame')
            return {'width': 100, 'height': 100, 'image': ''}

    result = asyncio.run(Device('emulator').inspect())
    assert calls[:3] == ['multi', 'frame', 'multi']
    if adb_available:
        assert calls[3:] == ['adb', 'frame', 'adb']
        assert result['nodes'] and result['treeSource'] == m.ACTIVE_WINDOW
    else:
        assert calls[-1] == 'frame' and result['width'] == 100
        assert result['nodes'] == [] and result['treeSource'] == m.SCREENSHOT_ONLY


def test_inspect_rejects_orientation_changed_after_screenshot(monkeypatch):
    device = m.Device('emulator')
    rotation = iter(['0', '1', '1', '1'])

    async def multi():
        return {**tree(), 'rotation': next(rotation)}

    async def frame():
        return {'width': 100, 'height': 100, 'image': ''}

    monkeypatch.setattr(device, 'multi_window_tree', multi)
    monkeypatch.setattr(device, 'frame', frame)
    assert asyncio.run(device.inspect())['rotation'] == '1'


def test_navigation_seeds_keep_system_windows_out_of_page_fingerprint(tmp_path):
    from app.ui_navigation import NavigationStore

    xml = ('<hierarchy><node package="com.android.systemui"/>'
           '<node package="demo"><node package="demo" text="Open" '
           'resource-id="demo:id/open" clickable="true" enabled="true" bounds="[0,0][50,50]"/>'
           '</node></hierarchy>')
    source = {**tree(), **m.parse_tree(xml), 'treeSource': m.MULTI_WINDOW}
    destination = {**tree('Destination'), 'treeSource': m.MULTI_WINDOW}
    db = m.MapStore(tmp_path)
    db.remember(source)
    db.remember(destination)
    graph = db.graph('dev')
    a, b = db.ensure_page(graph, source), db.ensure_page(graph, destination)
    graph['edges'].append({'id': m.uid(), 'from': a['id'], 'to': b['id'],
                           'status': 'verified', 'locator': m.locator(source, source['nodes'][2])})
    db.put('dev', graph)
    seeds = NavigationStore(tmp_path).load('dev')['seeds']
    nodes = next(p['nodes'] for p in seeds['pages'] if p['id'] == a['id'])
    assert [n['package'] for n in nodes] == ['demo', 'demo']
    assert [n['parent'] for n in nodes] == [None, 0]
    assert seeds['edges'][0]['selector']['resourceId'] == 'demo:id/open'


def test_graph_does_not_use_unverified_or_reverse_edges():
    g={'edges':[{'from':'a','to':'b','status':'observed'}]}
    with pytest.raises(ValueError):m.route(g,'a','b')
    g['edges'][0]['status']='verified'
    assert len(m.route(g,'a','b'))==1
    with pytest.raises(ValueError):m.route(g,'b','a')


@pytest.mark.parametrize('restore_order', [('parent', 'child'), ('child', 'parent')])
def test_subpages_remain_visible_and_links_restore_in_either_order(tmp_path, monkeypatch, restore_order):
    async def scenario():
        db, req, _ = setup(tmp_path, monkeypatch, [])
        graph = db.graph('dev')
        graph['pages'] = [
            {'id': 'parent', 'name': 'Parent', 'appId': 'demo'},
            {'id': 'child', 'name': 'Child', 'appId': 'demo', 'parentPageId': 'parent', 'parentEdgeId': 'pc',
             'navigationStatus': 'recorded', 'entry': {'sourcePageId': 'parent', 'elementId': 'entry'}},
            {'id': 'leaf', 'name': 'Leaf', 'appId': 'demo', 'parentPageId': 'child', 'parentEdgeId': 'cl',
             'navigationStatus': 'recorded'},
        ]
        graph['elements'] = [{'id': 'entry', 'pageId': 'parent'}]
        graph['edges'] = [
            {'id': 'pc', 'from': 'parent', 'to': 'child', 'status': 'verified'},
            {'id': 'cl', 'from': 'child', 'to': 'leaf', 'status': 'verified'},
        ]
        db.put('dev', graph)

        async def change(id_, action):
            result = await m.edit(m.EditRequest(serial='dev', id=id_, action=action), req)
            pages = {page['id']: page for page in result['pages']}
            edges = {edge['id']: edge for edge in result['edges']}
            for page in pages.values():
                assert not page.get('parentPageId') or page['parentPageId'] in pages
                assert not page.get('parentEdgeId') or page['parentEdgeId'] in edges
            return result, pages

        result, pages = await change('parent', 'delete')
        assert pages['child']['navigationStatus'] == 'missing'
        assert pages['child'].get('entry') is None
        with pytest.raises(ValueError):
            m.route(result, 'parent', 'leaf')
        _, pages = await change('child', 'delete')
        assert pages['leaf']['navigationStatus'] == 'missing'
        for page_id in restore_order:
            result, pages = await change(page_id, 'restore')
        assert pages['child']['entry']['elementId'] == 'entry'
        assert [edge['id'] for edge in m.route(result, 'parent', 'leaf')] == ['pc', 'cl']
        assert len(result['elements']) == 1

    asyncio.run(scenario())


def test_saved_element_persists_and_rename_keeps_locator(tmp_path,monkeypatch):
    async def scenario():
        obs=tree();db,req,_=setup(tmp_path,monkeypatch,[obs]);db.remember(obs)
        page_id=await save_app_page(req,obs)
        body=m.ElementRequest(serial='dev',observationId=obs['id'],nodeIndex=1,name='My toggle',pageId=page_id)
        g=await m.save(body,req);element=g['elements'][0]
        await m.save(body,req)
        assert len(db.graph('dev')['elements'])==1
        await m.edit(m.EditRequest(serial='dev',id=element['id'],action='rename',name='New name'),req)
        reloaded=m.MapStore(tmp_path).graph('dev')['elements'][0]
        assert reloaded['name']=='New name' and reloaded['locator']==element['locator']
        await m.edit(m.EditRequest(serial='dev',id=element['id'],action='delete'),req)
        assert not db.graph('dev')['elements']
        await m.edit(m.EditRequest(serial='dev',id=element['id'],action='restore'),req)
        assert len(db.graph('dev')['elements'])==1
    asyncio.run(scenario())


def test_stale_snapshot_cannot_click(tmp_path,monkeypatch):
    async def scenario():
        old=tree();db,req,fake=setup(tmp_path,monkeypatch,[tree('Different page')]);db.remember(old)
        with pytest.raises(HTTPException) as error:
            await m.tap(m.TapRequest(serial='dev',observationId=old['id'],nodeIndex=1),req)
        assert '快照不同' in error.value.detail and not fake.taps
    asyncio.run(scenario())


def test_set_checked_is_idempotent_and_duplicate_request_rejected(tmp_path,monkeypatch):
    async def scenario():
        obs=tree(checked=True);db,req,fake=setup(tmp_path,monkeypatch,[obs]);db.remember(obs)
        page_id=await save_app_page(req,obs)
        g=await m.save(m.ElementRequest(serial='dev',observationId=obs['id'],nodeIndex=1,pageId=page_id),req)
        body=m.ExecuteRequest(serial='dev',targetId=g['elements'][0]['id'],intent='on',requestId='one')
        result=await m.execute(body,req)
        assert '零点击' in result['message'] and not fake.taps
        with pytest.raises(HTTPException):await m.execute(body,req)
        assert not fake.taps
    asyncio.run(scenario())


def test_recording_switch_rejected_before_input(tmp_path,monkeypatch):
    async def scenario():
        obs=tree();db,req,fake=setup(tmp_path,monkeypatch,[obs]);db.remember(obs)
        with pytest.raises(HTTPException):await m.tap(m.TapRequest(serial='dev',observationId=obs['id'],nodeIndex=1,recordNavigation=True),req)
        assert not fake.taps
    asyncio.run(scenario())


def entrance(label):
    xml=f'<hierarchy rotation="0"><node package="demo" class="View" text="{label}" enabled="true" bounds="[0,0][100,100]"><node package="demo" class="Button" resource-id="demo:id/entry" text="Open" clickable="true" enabled="true" bounds="[10,20][90,60]" /></node></hierarchy>'
    return {**m.parse_tree(xml),'id':m.uid(),'serial':'dev','width':100,'height':100,'image':'','capturedAt':1}


def test_record_verify_replay_and_stop_on_wrong_destination(tmp_path, monkeypatch):
    async def scenario():
        a,b,c=entrance('A'),entrance('B'),entrance('Unexpected')
        observations=[a,b,a,b,a,b]
        db,req,fake=setup(tmp_path,monkeypatch,observations);db.remember(a)
        body=m.TapRequest(serial='dev',observationId=a['id'],nodeIndex=1,recordNavigation=True)
        first=await m.tap(body,req)
        assert first['graph']['edges'][0]['status']=='observed'
        second=await m.tap(body,req)
        edge=second['graph']['edges'][0]
        assert edge['status']=='verified'
        target=edge['to']
        result=await m.execute(m.ExecuteRequest(serial='dev',targetId=target,requestId='success'),req)
        assert result['state']=='succeeded' and len(fake.taps)==3
        observations[:]=[a,c]
        with pytest.raises(HTTPException) as exc:
            await m.execute(m.ExecuteRequest(serial='dev',targetId=target,requestId='wrong-page'),req)
        assert '路径失效' in exc.value.detail and len(fake.taps)==4
        assert db.graph('dev')['edges'][0]['status']=='stale'
    asyncio.run(scenario())


def test_replay_does_not_tap_entry_that_changed_to_switch(tmp_path, monkeypatch):
    async def scenario():
        source = entrance("A")
        target = entrance("B")
        changed = entrance("A")
        changed["nodes"][1]["checkable"] = True
        observations = [source, target, changed]
        db, req, fake = setup(tmp_path, monkeypatch, observations)
        db.remember(source)
        recorded = await m.tap(m.TapRequest(
            serial="dev", observationId=source["id"], nodeIndex=1, recordNavigation=True,
        ), req)
        edge, = recorded["graph"]["edges"]
        with pytest.raises(HTTPException, match="导航入口当前不可安全点击"):
            await m.execute(m.ExecuteRequest(serial="dev", targetId=edge["to"], requestId="unsafe"), req)
        assert len(fake.taps) == 1
        assert db.graph("dev")["edges"][0]["status"] == "stale"

    asyncio.run(scenario())


def test_busy_device_rejected_and_stale_swipe_sends_nothing(tmp_path, monkeypatch):
    async def scenario():
        old=tree();db,req,fake=setup(tmp_path,monkeypatch,[tree('Changed')]);db.remember(old)
        lock=req.app.state.ui.device_lock('dev')
        await lock.acquire()
        with pytest.raises(HTTPException) as exc:
            await m.observe(m.Serial(serial='dev'),req)
        assert exc.value.status_code==409
        lock.release()
        with pytest.raises(HTTPException) as exc:
            await m.swipe(m.SwipeRequest(serial='dev',observationId=old['id'],startX=.5,startY=.8,endX=.5,endY=.2),req)
        assert '未发送滑动' in exc.value.detail
    asyncio.run(scenario())


def test_operation_does_not_change_saved_tree(tmp_path, monkeypatch):
    async def scenario():
        a,b,c=entrance('A'),entrance('B'),entrance('C')
        db,req,fake=setup(tmp_path,monkeypatch,[a,b,b,c]);db.remember(a)
        before=db.graph('dev')
        result=await m.tap(m.TapRequest(serial='dev',observationId=a['id'],nodeIndex=1),req)
        await m.swipe(m.SwipeRequest(serial='dev',observationId=result['observation']['id'],startX=.5,startY=.8,endX=.5,endY=.2),req)
        assert db.graph('dev')==before and len(fake.taps)==1 and len(fake.swipes)==1
    asyncio.run(scenario())


def test_explicit_page_save_commits_temporary_entry_and_replays(tmp_path, monkeypatch):
    async def scenario():
        import time
        a,b=entrance('Parent'),entrance('Child')
        db,req,fake=setup(tmp_path,monkeypatch,[a,b]);db.remember(a);db.remember(b)
        parent=await save_app_page(req,a)
        g=db.graph('dev');app_id=g['apps'][0]['id']
        req.app.state.ui_drafts={'draft':{'serial':'dev','createdAt':time.time(),'complete':True,'sourceObservationId':a['id'],'steps':[{'action':'tap','startX':.5,'startY':.4,'endX':.5,'endY':.4}]}}
        assert not g['edges']
        saved=await m.save_page(m.PageSave(serial='dev',observationId=b['id'],appId=app_id,parentPageId=parent,draftToken='draft',name='Child'),req)
        assert saved['graph']['edges'][0]['status']=='observed'
        assert saved['graph']['pages'][1]['parentPageId']==parent
        result=await m.execute(m.ExecuteRequest(serial='dev',targetId=saved['pageId'],requestId='replay'),req)
        assert len(fake.taps)==1 and result['graph']['edges'][0]['status']=='verified'
    asyncio.run(scenario())


def test_package_launch_recovers_disconnected_page_and_migrates_old_map(tmp_path, monkeypatch):
    async def scenario():
        home,root,target=entrance('Desktop'),entrance('App root'),entrance('Detail')
        db,req,fake=setup(tmp_path,monkeypatch,[home,root,target])
        db.remember(root);db.remember(target)
        g=db.graph('dev');a=db.ensure_page(g,root);b=db.ensure_page(g,target)
        b.pop('package')
        g['edges'].append({'id':'edge','from':a['id'],'to':b['id'],'status':'observed','locator':m.locator(root,root['nodes'][1])})
        db.put('dev',g)
        result=await m.execute(m.ExecuteRequest(serial='dev',targetId=b['id'],requestId='launch'),req)
        assert result['state']=='succeeded' and fake.launches==['demo'] and len(fake.taps)==1
        assert db.graph('dev')['pages'][1]['package']=='demo'
    asyncio.run(scenario())


def test_package_launch_unknown_landing_stops_without_click(tmp_path, monkeypatch):
    async def scenario():
        target=entrance('Target');db,req,fake=setup(tmp_path,monkeypatch,[entrance('Desktop'),entrance('Unknown landing')])
        g=db.graph('dev');page=db.ensure_page(g,target);db.put('dev',g)
        with pytest.raises(HTTPException) as exc:
            await m.execute(m.ExecuteRequest(serial='dev',targetId=page['id'],requestId='unknown-landing'),req)
        assert '落地页尚未记录' in exc.value.detail and fake.launches==['demo'] and not fake.taps
    asyncio.run(scenario())


def test_launch_uses_resolved_launcher_and_rejects_missing_entry(monkeypatch):
    async def scenario():
        dev=m.Device('dev');calls=[]
        async def command(*args):
            calls.append(args)
            return b'com.demo/.MainActivity\n' if len(calls)==1 else b'Status: ok'
        monkeypatch.setattr(dev,'command',command)
        await dev.launch('com.demo')
        assert calls[0][:4]==('shell','cmd','package','resolve-activity')
        assert calls[1][-2:]==('-n','com.demo/.MainActivity')
        with pytest.raises(ValueError):await dev.launch('bad;input')
        assert len(calls)==2
        async def missing(*args):return b'No activity found'
        monkeypatch.setattr(dev,'command',missing)
        with pytest.raises(ValueError,match='没有可用'):await dev.launch('com.demo')
    asyncio.run(scenario())


def test_save_requires_page_and_rejects_wrong_owner(tmp_path, monkeypatch):
    async def scenario():
        a,b=entrance('A'),entrance('B');db,req,_=setup(tmp_path,monkeypatch,[a]);db.remember(a);db.remember(b)
        with pytest.raises(HTTPException):await m.save(m.ElementRequest(serial='dev',observationId=a['id'],nodeIndex=1),req)
        page=await save_app_page(req,a)
        with pytest.raises(HTTPException):await m.save(m.ElementRequest(serial='dev',observationId=b['id'],nodeIndex=1,pageId=page),req)
    asyncio.run(scenario())


def test_move_and_undo_preserve_locator_and_cross_app_requires_recapture(tmp_path, monkeypatch):
    async def scenario():
        a,b=entrance('A'),entrance('B');c=entrance('C')
        for n in c['nodes']:n['package']='other.app'
        c['signature']='other'
        db,req,_=setup(tmp_path,monkeypatch,[a])
        for o in (a,b,c):db.remember(o)
        pages=[await save_app_page(req,o) for o in (a,b,c)]
        g=await m.save(m.ElementRequest(serial='dev',observationId=a['id'],nodeIndex=1,pageId=pages[0]),req)
        original=g['elements'][0].copy()
        g=await m.move_element(m.MoveElement(serial='dev',elementId=original['id'],pageId=pages[1]),req)
        assert g['elements'][0]['validation']=='pending' and g['elements'][0]['locator']==original['locator']
        g=await m.move_element(m.MoveElement(serial='dev',elementId=original['id'],undo=True),req)
        assert g['elements'][0]==original
        g=await m.move_element(m.MoveElement(serial='dev',elementId=original['id'],pageId=pages[2]),req)
        assert g['elements'][0]['validation']=='recapture'
        g=await m.save(m.ElementRequest(serial='dev',observationId=c['id'],nodeIndex=1,pageId=pages[2],elementId=original['id']),req)
        assert len(g['elements'])==1 and g['elements'][0]['validation']=='verified'
        assert g['elements'][0]['locator']['fields']['package']=='other.app'
    asyncio.run(scenario())


def test_page_identity_ignores_dynamic_values_not_titles_tabs_or_dialogs():
    a=entrance('Battery');b=entrance('Battery')
    a['nodes'][1].update(resourceId='demo:id/widget_summary',text='58分钟前')
    b['nodes'][1].update(resourceId='demo:id/widget_summary',text='59分钟前')
    assert m.identity(a)==m.identity(b)
    b['nodes'][0]['text']='Display'
    assert m.identity(a)!=m.identity(b)
    b['nodes'][0]['text']='Battery';b['nodes'][0]['selected']=True
    assert m.identity(a)!=m.identity(b)
    b['nodes'][0]['selected']=False;b['nodes'].append(dict(b['nodes'][1],package='android',text='Dialog'))
    assert m.identity(a)!=m.identity(b)


def test_missing_path_is_explicit_and_page_cycle_rejected(tmp_path, monkeypatch):
    async def scenario():
        a,b=entrance('A'),entrance('B');db,req,_=setup(tmp_path,monkeypatch,[a]);db.remember(a);db.remember(b)
        parent=await save_app_page(req,a);app=db.graph('dev')['apps'][0]['id']
        body=m.PageSave(serial='dev',observationId=b['id'],appId=app,parentPageId=parent)
        with pytest.raises(HTTPException):await m.save_page(body,req)
        body.allowMissing=True
        result=await m.save_page(body,req);child=result['pageId']
        assert result['graph']['pages'][1]['navigationStatus']=='missing'
        with pytest.raises(HTTPException):await m.save_page(m.PageSave(serial='dev',observationId=a['id'],appId=app,parentPageId=child,allowMissing=True),req)
    asyncio.run(scenario())


def test_scrcpy_touch_packet_bounds():
    from app.ui_stream import touch_packet
    assert len(touch_packet(0,10,20,1080,1920))==32
    with pytest.raises(ValueError):touch_packet(0,1080,20,1080,1920)


def test_legacy_migration_keeps_ids_and_only_recovers_proven_dynamic_failure(tmp_path, monkeypatch):
    import json
    a,b=entrance('Settings'),entrance('Battery')
    b['nodes'][1].update(resourceId='demo:id/widget_summary',text='58分钟前')
    actual={**b,'id':m.uid(),'signature':'changed','nodes':[dict(n) for n in b['nodes']]}
    actual['nodes'][1]['text']='59分钟前'
    db,req,_=setup(tmp_path,monkeypatch,[a]);db.remember(a);db.remember(b);db.remember(actual)
    legacy={'revision':4,'pages':[{'id':'root','name':'用户首页','signature':a['signature'],'parentEdgeId':None},{'id':'battery','name':'用户电池','signature':b['signature'],'parentEdgeId':'edge'}],
            'elements':[{'id':'button','name':'用户别名','pageId':'battery','locator':m.locator(b,b['nodes'][1])}],
            'edges':[{'id':'edge','from':'root','to':'battery','status':'stale'},{'id':'unrelated','from':'battery','to':'root','status':'stale'}],'deleted':[]}
    with db.connect() as c:
        c.execute('INSERT INTO maps VALUES (?,?)',('dev',json.dumps(legacy)))
        c.execute('INSERT INTO executions VALUES (?,?,?)',('failure','dev',json.dumps({'message':'入口未到达目标页','lastObservationId':actual['id']})))
    g=db.graph('dev')
    assert g['schemaVersion']==2 and len(g['apps'])==1
    assert g['pages'][1]['parentPageId']=='root' and g['elements'][0]['name']=='用户别名'
    assert g['edges'][0]['status']=='observed' and g['edges'][1]['status']=='stale'
    assert db.page(g,actual)['id']=='battery'
    assert (tmp_path/'maps-before-tree-v2.db').exists()
    assert db.graph('dev')==g


def test_parent_element_promotion_preserves_complete_entry_evidence(tmp_path, monkeypatch):
    import time
    async def scenario():
        a,b=entrance('Settings'),entrance('Display');a['image']='source-png';b['image']='destination-png'
        db,req,_=setup(tmp_path,monkeypatch,[a,b]);db.remember(a);db.remember(b)
        app=await m.save_app(m.AppSave(serial='dev',observationId=a['id'],name='Settings'),req)
        # An ordinary element may be saved directly under the application, without creating a page manually.
        g=await m.save(m.ElementRequest(serial='dev',observationId=a['id'],nodeIndex=1,appId=app['appId'],name='My display entry'),req)
        el=g['elements'][0];parent=el['pageId']
        req.app.state.ui_drafts={'d':{'serial':'dev','createdAt':time.time(),'complete':True,'sourceObservationId':a['id'],'steps':[{'action':'tap','startX':.5,'startY':.4,'endX':.5,'endY':.4}]}}
        result=await m.save_page(m.PageSave(serial='dev',observationId=b['id'],appId=app['appId'],parentPageId=parent,draftToken='d',entryElementId=el['id']),req)
        target=next(p for p in result['graph']['pages'] if p['id']==result['pageId'])
        assert target['name']=='My display entry' and target['entry']['elementId']==el['id']
        props=await m.parent_element(m.ParentRead(serial='dev',pageId=target['id']),req)
        assert props['observation']['image']=='source-png'
        assert props['node']['bounds']==a['nodes'][1]['bounds']
        assert props['node']['resourceId']=='demo:id/entry' and props['node']['xpath']==a['nodes'][1]['xpath']
        assert target['observationId']==b['id']  # Destination proof is separate from the source control screenshot.
        child=await m.save(m.ElementRequest(serial='dev',observationId=b['id'],nodeIndex=1,pageId=target['id'],name='Child'),req)
        assert child['elements'][-1]['pageId']==target['id']
        with pytest.raises(HTTPException):
            await m.move_element(m.MoveElement(serial='dev',elementId=el['id'],pageId=target['id']),req)
    asyncio.run(scenario())


def test_legacy_parent_backfill_uses_source_not_destination(tmp_path, monkeypatch):
    a,b=entrance('Source'),entrance('Destination');db,req,_=setup(tmp_path,monkeypatch,[a]);db.remember(a);db.remember(b)
    g=db.graph('dev');p=db.ensure_page(g,a);q=db.ensure_page(g,b)
    edge={'id':'e','from':p['id'],'to':q['id'],'status':'verified','locator':m.locator(a,a['nodes'][1])}
    g['edges']=[edge];q['parentEdgeId']='e';g.pop('entryVersion');db.put('dev',g)
    migrated=db.graph('dev');target=migrated['pages'][1]
    assert target['entry']['observationId']==a['id'] and target['entry']['nodeIndex']==1
    assert migrated['pages'][0].get('entry') is None
    assert (tmp_path/'maps-before-parent-elements.db').exists()
    assert db.graph('dev')==migrated


def test_parent_entry_can_launch_another_package(tmp_path, monkeypatch):
    import time
    async def scenario():
        a,b=entrance('AppList'),entrance('Settings')
        for n in b['nodes']: n['package']='com.android.settings'
        b['signature']='settings-package'
        db,req,_=setup(tmp_path,monkeypatch,[a,b]);db.remember(a);db.remember(b)
        app_a=await m.save_app(m.AppSave(serial='dev',observationId=a['id']),req)
        g=await m.save(m.ElementRequest(serial='dev',observationId=a['id'],nodeIndex=1,appId=app_a['appId']),req)
        el=g['elements'][0]
        app_b=await m.save_app(m.AppSave(serial='dev',observationId=b['id']),req)
        req.app.state.ui_drafts={'d':{'serial':'dev','createdAt':time.time(),'complete':True,'sourceObservationId':a['id'],'steps':[{'action':'tap','startX':.5,'startY':.4,'endX':.5,'endY':.4}]}}
        saved=await m.save_page(m.PageSave(serial='dev',observationId=b['id'],appId=app_b['appId'],parentPageId=el['pageId'],entryElementId=el['id'],draftToken='d'),req)
        p=next(p for p in saved['graph']['pages'] if p['id']==saved['pageId'])
        assert p['parentPageId'] is None and p['appId']==app_b['appId']
        assert p['entry']['sourcePageId']==el['pageId']
        result=await m.execute(m.ExecuteRequest(serial='dev',targetId=p['id'],requestId='cross-app-entry'),req)
        assert result['state']=='succeeded'
    asyncio.run(scenario())
