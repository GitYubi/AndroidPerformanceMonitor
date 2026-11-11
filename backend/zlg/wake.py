"""DBC encoding rules for user-configured wake frames."""

import math

from cantools.database import EncodeError


def parse_signal_number(text):
    try:
        return int(text, 10)
    except ValueError:
        try:
            value = float(text)
        except ValueError:
            value = math.nan
        if not math.isfinite(value):
            raise ValueError("请输入有效的数值。")
        return value


def resolve_signal_value(signal, value, message_name):
    if isinstance(value, dict):
        raw = value.get("raw")
        if type(raw) is not int or not signal.choices or raw not in signal.choices:
            raise ValueError(f"{message_name}.{signal.name} 的枚举值无效。")
        value = signal.raw_to_scaled(raw, decode_choices=False)
        if signal.scaled_to_raw(value) != raw:
            raise ValueError(f"{message_name}.{signal.name} 的枚举值无法精确转换。")
    elif type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{message_name}.{signal.name} 需要有效的数值。")
    else:
        if ((signal.minimum is not None and value < signal.minimum)
                or (signal.maximum is not None and value > signal.maximum)):
            raise ValueError(f"{message_name}.{signal.name} 超出 DBC 范围。")
        try:
            raw = signal.scaled_to_raw(value)
            represented = signal.raw_to_scaled(raw, decode_choices=False)
            if not signal.is_float and represented != value:
                if not isinstance(represented, float) or not math.isclose(
                        represented, value, rel_tol=0, abs_tol=abs(signal.scale) * 1e-8):
                    raise ValueError("该值不符合 DBC 信号步长")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{message_name}.{signal.name} 无法编码：{exc}。") from exc
    return value


def encode_wake_message(message, selected, previous=None):
    """Keep unspecified live values, or use the DBC initial value (raw zero if absent)."""
    if not selected or message.is_container:
        raise ValueError(f"{message.name} 需要配置至少一个普通信号。")
    values = {
        signal.name: (signal.initial if signal.initial is not None
                      else signal.raw_to_scaled(0, decode_choices=False))
        for signal in message.signals
    }
    values.update(previous or {})
    for name, value in selected.items():
        try:
            signal = message.get_signal_by_name(name)
        except KeyError as exc:
            raise ValueError(f"{message.name} 没有信号 {name}。") from exc
        values[name] = resolve_signal_value(signal, value, message.name)

    try:
        raw_values = {signal.name: signal.scaled_to_raw(values[signal.name])
                      for signal in message.signals}
        active = message.gather_signals(raw_values)
        if inactive := selected.keys() - active.keys():
            raise ValueError(f"复用选择值未启用这些信号：{', '.join(sorted(inactive))}")
        payload = message.encode(active, scaling=False, strict=False)
    except (EncodeError, ValueError, OverflowError, KeyError, TypeError) as exc:
        raise ValueError(f"{message.name} 无法编码：{exc}") from exc
    return values, payload.hex()
