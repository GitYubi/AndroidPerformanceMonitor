"""Interruptible periodic deadlines on a monotonic clock."""

from dataclasses import dataclass, field
from itertools import count
import threading
from time import perf_counter_ns

from can import Message

_TASK_IDS = count()


@dataclass
class HardwarePeriodicSend:
    frame: Message
    slot: int
    period_ms: int
    task_id: int = field(default_factory=lambda: next(_TASK_IDS), init=False)
    active: bool = True
    submitted: bool = True
    error: str | None = None

    @property
    def channel(self):
        return self.frame.channel

    @property
    def payload(self):
        return bytes(self.frame.data)

    def is_alive(self):
        return self.active


class Deadline:
    def __init__(self, period_ns, now_ns):
        self.period_ns = period_ns
        self.next_ns = now_ns

    def rearm(self, period_ns, now_ns):
        if period_ns != self.period_ns:
            self.period_ns = period_ns
            self.next_ns = now_ns + period_ns

    def advance(self, due_ns, completed_ns):
        skipped = max(0, (completed_ns - due_ns) // self.period_ns)
        self.next_ns = due_ns + (skipped + 1) * self.period_ns
        return skipped


class PeriodicSend(threading.Thread):
    def __init__(self, frame, period_ns, dispatch, log, clock=perf_counter_ns, *, send_count=-1):
        super().__init__(daemon=True)
        self.task_id = next(_TASK_IDS)
        self._frame = frame
        self._clock = clock
        self._deadline = Deadline(period_ns, clock())
        self._dispatch = dispatch
        self._log = log
        self._condition = threading.Condition()
        self._stopping = False
        self.remaining = None if send_count == -1 else send_count
        self.submitted = False
        self.error = None

    @property
    def channel(self):
        return self._frame.channel

    @property
    def payload(self):
        return bytes(self._frame.data)

    @property
    def frame(self):
        return self._frame

    @property
    def period_ms(self):
        return self._deadline.period_ns // 1_000_000

    def record_submission(self):
        with self._condition:
            self.submitted = True
            if self.remaining is not None:
                self.remaining -= 1
                if self.remaining == 0:
                    self._stopping = True

    def update(self, frame, period_ns):
        with self._condition:
            if self._stopping:
                return False
            self._frame = frame
            self._deadline.rearm(period_ns, self._clock())
            self._condition.notify()
            return True

    def pending(self, now_ns):
        with self._condition:
            due = self._deadline.next_ns
            if self._stopping or now_ns < due:
                return None
            return self._frame, due, self._deadline.period_ns

    def advance(self, due_ns, completed_ns):
        with self._condition:
            return self._deadline.advance(due_ns, completed_ns)

    def run(self):
        try:
            while True:
                with self._condition:
                    while not self._stopping:
                        remaining = self._deadline.next_ns - self._clock()
                        if remaining <= 0:
                            break
                        self._condition.wait(min(remaining / 1_000_000_000, threading.TIMEOUT_MAX))
                    if self._stopping:
                        return
                # Do not hold the condition while waiting for the device I/O lock.
                self._dispatch(self)
        except (RuntimeError, OSError) as exc:
            self.error = str(exc)
            self._log.error(f"周期发送停止：{exc}")

    def cancel(self):
        with self._condition:
            self._stopping = True
            self._condition.notify()

    def stop(self):
        self.cancel()
        self.join()


class PulseSend(PeriodicSend):
    def __init__(self, frame, reset_frame, period_ns, dispatch, log, *, send_count, resume=None):
        super().__init__(frame, period_ns, dispatch, log, send_count=send_count)
        self._reset_frame = reset_frame
        self._phase_count = send_count
        self._resume = resume
        self._cancelled = threading.Event()
        self._last_payload = bytes(frame.data)

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    @property
    def payload(self):
        return self._last_payload

    def record_submission(self):
        with self._condition:
            self._last_payload = bytes(self.frame.data)
            super().record_submission()
            if self.remaining == 0 and self._reset_frame is not None and not self.cancelled:
                self._frame = self._reset_frame
                self._reset_frame = None
                self.remaining = self._phase_count
                self._stopping = False

    def cancel(self):
        self._cancelled.set()
        super().cancel()

    def run(self):
        super().run()
        if self.remaining == 0 and self._resume is not None:
            delay = max(0, self._deadline.next_ns - self._clock()) / 1_000_000_000
            if not self._cancelled.wait(min(delay, threading.TIMEOUT_MAX)):
                self._resume(self)
