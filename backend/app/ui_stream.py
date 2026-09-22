"""Pinned scrcpy 3.3.4 H264/control bridge. No hierarchy polling or tree writes."""
import asyncio
import json
import re
import struct
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, WebSocket
from .ui_map import Device

router = APIRouter(prefix='/api/ui/map')
SERVER = Path(__file__).resolve().parents[1] / 'vendor/scrcpy/scrcpy-server-v3.3.4'
VERSION = '3.3.4'


def touch_packet(action, x, y, width, height):
    if action not in (0, 1, 2, 3) or not (1 <= width <= 65535 and 1 <= height <= 65535):
        raise ValueError('无效触摸事件')
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError('触摸超出画面')
    return struct.pack('>BBQiiHHHII', 2, action, 0, x, y, width, height, 0 if action in (1, 3) else 65535, 0, 0)


@router.websocket('/stream/{serial}')
async def stream(ws: WebSocket, serial: str):
    origin = ws.headers.get('origin', '')
    if not re.fullmatch(r'https?://(localhost|127\.0\.0\.1)(:\d+)?', origin) or not re.fullmatch(r'[a-zA-Z0-9._:-]{1,128}', serial):
        await ws.close(code=1008); return
    await ws.accept()
    ui = ws.app.state.ui; lock = ui.device_lock(serial)
    if lock.locked() or ui.has_active_device(serial):
        await ws.send_json({'error': '设备正在使用，请停止其他任务后重试'}); await ws.close(); return
    traces = ws.app.state.ui_drafts
    # Temporary recordings expire; nothing is persisted until explicit page save.
    for key in list(traces):
        if time.time() - traces[key]['createdAt'] > 1800:
            del traces[key]
    token = uuid.uuid4().hex
    draft = {'serial': serial, 'createdAt': time.time(), 'steps': [], 'sourceObservationId': None, 'complete': False}
    traces[token] = draft
    proc = None; port = None; writers = []; tasks = []; logs = bytearray(); touching = None
    dev = Device(serial)
    await lock.acquire()
    try:
        init = await asyncio.wait_for(ws.receive_json(), 10)
        if init.get('observationId'):
            ws.app.state.ui_map.observation(serial, init['observationId'])
            draft['sourceObservationId'] = init['observationId']
        if not SERVER.exists():
            raise ValueError('scrcpy 服务文件缺失，请运行 tools/setup-scrcpy.sh')
        scid = uuid.uuid4().hex[:8]; scid = f'{int(scid, 16) & 0x7fffffff:08x}'
        remote = f'/data/local/tmp/ui-map-scrcpy-{scid}.jar'
        await dev.command('push', str(SERVER), remote)
        port = (await dev.command('forward', 'tcp:0', f'localabstract:scrcpy_{scid}')).decode().strip()
        proc = await asyncio.create_subprocess_exec('adb', '-s', serial, 'shell', f'CLASSPATH={remote}', 'app_process', '/',
            'com.genymobile.scrcpy.Server', VERSION, f'scid={scid}', 'tunnel_forward=true', 'audio=false',
            'video_codec=h264', 'max_size=1600', 'max_fps=30', 'video_bit_rate=4000000', 'clipboard_autosync=false',
            'power_on=false', 'cleanup=true', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        async def drain_logs():
            while chunk := await proc.stdout.read(4096):
                logs.extend(chunk)
                del logs[:-8000]
        tasks.append(asyncio.create_task(drain_logs()))
        for _ in range(100):
            try:
                video, vw = await asyncio.open_connection('127.0.0.1', int(port))
                await asyncio.wait_for(video.readexactly(1), .3)
                writers.append(vw)
                break
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                if 'vw' in locals(): vw.close()
                if proc.returncode is not None: raise ValueError(logs.decode(errors='replace'))
                await asyncio.sleep(.05)
        else:
            raise ValueError('scrcpy 视频连接超时：' + logs.decode(errors='replace')[-500:])
        control, cw = await asyncio.open_connection('127.0.0.1', int(port)); writers.append(cw)
        await asyncio.wait_for(video.readexactly(64), 10)
        codec, width, height = struct.unpack('>III', await video.readexactly(12))
        if codec != 0x68323634: raise ValueError('视频编码不是 H264')
        await ws.send_json({'ready': True, 'token': token, 'width': width, 'height': height})
        async def video_loop():
            while True:
                header = await video.readexactly(12)
                size = struct.unpack('>I', header[8:])[0]
                if size > 8_000_000: raise ValueError('视频帧超限')
                await ws.send_bytes(header + await video.readexactly(size))
        async def input_loop():
            nonlocal touching
            down = None
            while True:
                value = await ws.receive_json()
                if value.get('type') == 'stop':
                    draft['complete'] = True
                    return
                if value.get('type') != 'touch': continue
                action, x, y, w, h = (int(value[k]) for k in ('action', 'x', 'y', 'width', 'height'))
                cw.write(touch_packet(action, x, y, w, h)); await cw.drain()
                touching = (x, y, w, h) if action in (0, 2) else None
                if action == 0:
                    down = (x/w, y/h)
                elif action == 1 and down:
                    end = (x/w, y/h)
                    kind = 'tap' if abs(end[0]-down[0]) + abs(end[1]-down[1]) < .02 else 'swipe'
                    if len(draft['steps']) < 100:
                        draft['steps'].append({'action': kind, 'startX': down[0], 'startY': down[1], 'endX': end[0], 'endY': end[1]})
                    else: draft['overflow'] = True
                    down = None
                elif action == 3: down = None
        video_task = asyncio.create_task(video_loop()); input_task = asyncio.create_task(input_loop())
        tasks += [video_task, input_task]
        done, _ = await asyncio.wait([video_task, input_task], return_when=asyncio.FIRST_COMPLETED)
        for task in done: task.result()
    except Exception as exc:
        try: await ws.send_json({'error': str(exc)[:800] or '设备连接已断开'})
        except Exception: pass
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if touching and 'cw' in locals():
            try:
                cw.write(touch_packet(3, *touching)); await asyncio.wait_for(cw.drain(), .2)
            except Exception: pass
        for writer in writers: writer.close()
        if proc and proc.returncode is None:
            proc.terminate()
            try: await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError: proc.kill(); await proc.wait()
        if port:
            try: await dev.command('forward', '--remove', f'tcp:{port}')
            except Exception: pass
        if 'remote' in locals():
            try: await dev.command('shell', 'rm', '-f', remote)
            except Exception: pass
        lock.release()
        try: await ws.close()
        except Exception: pass
