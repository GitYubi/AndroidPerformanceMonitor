"""CAN transport contract shared by the Windows SDK and the local simulator."""

from collections import deque
from copy import copy
from time import time
from typing import Protocol

from can import Message


class CanBackend(Protocol):
    supports_diagnostics: bool

    @property
    def channels(self) -> tuple[int, ...]: ...

    def open_channel(self, channel: int) -> None: ...
    def close_channel(self, channel: int) -> None: ...
    def send(self, message: Message) -> None: ...
    def poll(self, limit: int) -> list[Message]: ...
    def clear(self, channel: int) -> None: ...
    def is_online(self) -> bool: ...
    def close(self) -> None: ...


def validate_channel(channel: int) -> None:
    if type(channel) is not int or channel not in (0, 1):
        raise ValueError("通道必须为 0 或 1。")


class LoopbackBackend:
    """Two local channels with bounded transmit echo. No USB or CAN bus access."""

    supports_diagnostics = False

    def __init__(self):
        self._channels: set[int] = set()
        self._frames: deque[Message] = deque(maxlen=4096)
        self._closed = False

    @property
    def channels(self):
        return tuple(sorted(self._channels))

    def open_channel(self, channel):
        validate_channel(channel)
        if self._closed:
            raise RuntimeError("模拟设备已关闭。")
        self._channels.add(channel)

    def close_channel(self, channel):
        self._channels.discard(channel)
        self.clear(channel)

    def send(self, message):
        if message.channel not in self._channels:
            raise RuntimeError(f"通道 {message.channel} 未打开。")
        echo = copy(message)
        echo.data = bytearray(message.data)
        echo.is_rx = False
        echo.timestamp = time()
        self._frames.append(echo)

    def poll(self, limit):
        return [self._frames.popleft() for _ in range(min(limit, len(self._frames)))]

    def clear(self, channel):
        self._frames = deque((frame for frame in self._frames if frame.channel != channel),
                             maxlen=self._frames.maxlen)

    def is_online(self):
        return not self._closed

    def close(self):
        self._closed = True
        self._channels.clear()
        self._frames.clear()
