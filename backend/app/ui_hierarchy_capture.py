"""Read the UIAutomator2 accessibility windows on the default display."""
import contextlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


def default_display_xml(xml, display_ids, focus=None, read_windows=None):
    if len(xml.encode()) > 8_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:
        raise ValueError('控件树 XML 无效')
    root = ET.fromstring(xml)
    if root.tag != 'hierarchy':
        raise ValueError('控件树没有 hierarchy 根节点')
    for window in root.findall('node'):
        display = window.get('display-id', window.get('displayId'))
        if display is None and display_ids != {0}:
            raise ValueError('无法确认控件窗口属于默认屏幕')
        if display not in (None, '0'):
            root.remove(window)
    windows = root.findall('node')
    if not windows:
        raise ValueError('默认屏幕没有返回控件节点')
    if focus:
        package, _ = focus
        candidates = [i for i, window in enumerate(windows) if window.get('package') == package]
        if len(candidates) > 1:
            try:
                frame = focused_window_frame(read_windows(), focus) if read_windows else None
            except (OSError, subprocess.SubprocessError):
                frame = None
            candidates = [i for i in candidates if frame is not None and windows[i].get('bounds') == frame]
        root.set('application-root', str(candidates[0]) if len(candidates) == 1 else '')
    return ET.tostring(root, encoding='utf-8')


def adb_dump(serial, service, *args):
    return subprocess.check_output(
        ['adb', '-s', serial, 'shell', 'dumpsys', service, *args],
        timeout=5,
        encoding='utf-8',
        errors='replace',
    )


DISPLAY_HEADER = re.compile(r'^\s*Display:\s*mDisplayId=(\d+)\b.*$', re.M)


def display_ids(serial):
    return {int(value) for value in DISPLAY_HEADER.findall(adb_dump(serial, 'window', 'displays'))}


def normalized_component(component):
    package, name = component.split('/', 1)
    return package, package + name if name.startswith('.') else name


def default_focus(display_dump):
    sections = DISPLAY_HEADER.split(display_dump)
    for display, body in zip(sections[1::2], sections[2::2]):
        if display != '0':
            continue
        match = re.search(r'^\s*mFocusedApp=ActivityRecord\{[^\n]*?\bu\d+\s+(\S+/\S+)', body, re.M)
        if match:
            return normalized_component(match.group(1).rstrip('}'))
    return None


def focused_window_frame(window_dump, component):
    sections = re.split(r'^\s*Window #\d+ Window\{[^\n]*?\bu\d+\s+([^\n}]+)\}:\s*$', window_dump, flags=re.M)
    frames = set()
    for name, body in zip(sections[1::2], sections[2::2]):
        if '/' not in name or normalized_component(name) != component:
            continue
        display = re.search(r'^\s*mDisplayId=(\d+)\b', body, re.M)
        if not display or display.group(1) != '0' or 'ty=BASE_APPLICATION' not in body:
            continue
        frame = re.search(r'^\s*Frames:.*?\bframe=(\[-?\d+,-?\d+\]\[-?\d+,-?\d+\])', body, re.M)
        if frame:
            frames.add(frame.group(1))
    return next(iter(frames)) if len(frames) == 1 else None


def default_physical_display(display_dump):
    sections = re.split(r'^\s*Display (\d+):\s*$', display_dump, flags=re.M)
    for display, body in zip(sections[1::2], sections[2::2]):
        if display != '0':
            continue
        match = re.search(r'^\s*mPrimaryDisplayDevice=.*?\(local:(\d+)\)', body, re.M)
        if not match:
            match = re.search(r'^\s*mBaseDisplayInfo=.*?\buniqueId "local:(\d+)"', body, re.M)
        if match:
            return match.group(1)
    raise ValueError('无法确认默认屏幕对应的物理显示器')


def capture_default_screen(serial):
    physical_id = default_physical_display(adb_dump(serial, 'display'))
    return subprocess.check_output(
        ['adb', '-s', serial, 'exec-out', 'screencap', '-p', '-d', physical_id], timeout=12)


def check_default_focus(serial, displays):
    if displays == {0}:
        return
    output = subprocess.check_output(
        ['adb', '-s', serial, 'shell', 'dumpsys', 'window'],
        timeout=5,
        encoding='utf-8',
        errors='replace',
    )
    if not re.search(r'^\s*mTopFocusedDisplayId=0\b', output, re.M):
        raise ValueError('当前焦点不在默认屏幕')


def main():
    serial, *args = sys.argv[1:]
    try:
        if args == ['--capture-default']:
            sys.stdout.buffer.write(capture_default_screen(serial))
            return 0
        display_dump = adb_dump(serial, 'window', 'displays')
        displays = {int(value) for value in DISPLAY_HEADER.findall(display_dump)}
        if args == ['--check-active-display']:
            check_default_focus(serial, displays)
            return 0
        with contextlib.redirect_stdout(sys.stderr):
            import uiautomator2
            xml = uiautomator2.connect(serial).dump_hierarchy(compressed=False)
            result = default_display_xml(
                xml, displays, default_focus(display_dump), lambda: adb_dump(serial, 'window', 'windows'))
        sys.stdout.buffer.write(result)
    except Exception as exc:
        print(f'默认屏幕控件树读取失败 ({type(exc).__name__})', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
