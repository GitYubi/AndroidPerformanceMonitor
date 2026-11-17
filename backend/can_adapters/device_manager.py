"""Collect independent driver enumeration results and report unavailable drivers."""
from can_adapters import hirain


class CanDeviceManager:
    def discover(self):
        devices, errors = [], {}
        try:
            devices.extend(hirain.discover())
        except (RuntimeError, OSError) as exc:
            errors['hirain'] = str(exc)
        # Existing ZLG SDK wrapper opens a specified type/index; it exposes no
        # trustworthy non-invasive enumeration. Never infer presence from a DLL.
        errors['zlg'] = '现有周立功驱动封装尚未实现可靠枚举，请使用原有连接方式。'
        return {'devices': devices, 'errors': errors}
