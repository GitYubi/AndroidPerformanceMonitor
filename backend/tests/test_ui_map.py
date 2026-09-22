import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import ui_map as m


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


def test_graph_does_not_use_unverified_or_reverse_edges():
    g={'edges':[{'from':'a','to':'b','status':'observed'}]}
    with pytest.raises(ValueError):m.route(g,'a','b')
    g['edges'][0]['status']='verified'
    assert len(m.route(g,'a','b'))==1
    with pytest.raises(ValueError):m.route(g,'b','a')


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
