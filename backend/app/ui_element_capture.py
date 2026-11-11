"""Capture a uniquely identified Android element as a PNG for the UI runner."""
from __future__ import annotations

import contextlib
import io
import json
import sys


class ElementNotFound(Exception):
    pass


class ElementNotUnique(Exception):
    pass


def capture_element(device, selector):
    from uiautomator2.xpath import safe_xmlstr

    if not any(selector.get(key, '').strip() for key in ('resourceId', 'label', 'className', 'package')):
        raise ValueError('Missing selector')
    matches = []
    for element in device.xpath('//*[@bounds]').all():
        attributes = element.attrib
        if selector.get('resourceId') and attributes.get('resource-id') != selector['resourceId']:
            continue
        if selector.get('package') and attributes.get('package') != selector['package']:
            continue
        if selector.get('label') and (attributes.get('password') == 'true' or selector['label'] not in (
            attributes.get('text'), attributes.get('content-desc')
        )):
            continue
        left, top, right, bottom = element.bounds
        if left < 0 or top < 0 or right <= left or bottom <= top:
            continue
        if selector.get('className') and element.info['className'] != safe_xmlstr(selector['className']):
            continue
        matches.append(element)
        if len(matches) > 1:
            raise ElementNotUnique()
    if not matches:
        raise ElementNotFound()
    return matches[0].screenshot()


def main():
    try:
        import uiautomator2 as u2
    except ImportError:
        return 2
    try:
        serial, selector = sys.argv[1:]
        with contextlib.redirect_stdout(sys.stderr):
            device = u2.connect(serial)
            image = capture_element(device, json.loads(selector))
        output = io.BytesIO()
        image.save(output, format='PNG')
        sys.stdout.buffer.write(output.getvalue())
    except ElementNotFound:
        return 3
    except ElementNotUnique:
        return 4
    except Exception:
        return 5
    return 0


if __name__ == '__main__':
    sys.exit(main())
