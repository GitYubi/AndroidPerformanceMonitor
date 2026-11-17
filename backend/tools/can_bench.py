"""Opt-in CAN timing bench; never transmit on an unapproved vehicle network."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can import Message, Bus
from zlg.periodic import PeriodicSend
from zlg.zlgcan import open_backend

SCENARIOS = {'single10': [10], 'single20': [20], 'dual': [10, 50],
             'triple': [10, 50, 500], 'multi': [10, 20, 50, 100, 500], 'update': [10]}


def stats(values):
    values = sorted(values)
    def percentile(p):
        return values[max(0, math.ceil(len(values) * p) - 1)] if values else None
    return {'count': len(values), 'max': max(values) if values else None,
            'p95': percentile(.95), 'p99': percentile(.99)}


def run(args):
    if not args.confirm_safe:
        raise ValueError('必须提供 --confirm-safe，确认已获准在安全台架发送。')
    if not math.isfinite(args.duration) or args.duration <= 0 or args.channel not in (0, 1, 2, 3):
        raise ValueError('duration 必须是有限正数，通道必须在 0 到 3 之间。')
    periods = SCENARIOS[args.scenario]
    if args.base_id < 0 or args.base_id + len(periods) - 1 > 0x7ff:
        raise ValueError('标准帧 ID 必须在 0..0x7ff 范围。')
    if bool(args.listener_interface) != bool(args.listener_channel):
        raise ValueError('独立监听器的 interface 和 channel 必须同时指定。')
    os.environ['ZCAN_BACKEND'] = args.backend
    backend = listener = observer = None
    workers, host_rows, bus_rows, errors = [], [], [], []
    io_lock = threading.Lock()
    stop = threading.Event()
    class Logger:
        def error(self, message):
            errors.append(message)
    logger = Logger()
    ids = [args.base_id + i for i in range(len(periods))]

    def dispatch(worker):
        with io_lock:
            now = time.perf_counter_ns()
            pending = worker.pending(now)
            if pending is None:
                return
            frame, due, _ = pending
            started = time.time_ns()
            backend.send(frame)
            host_rows.append((started, time.time_ns(), frame.arbitration_id, bytes(frame.data).hex(), now - due))
            worker.record_submission()
            worker.advance(due, time.perf_counter_ns())

    def observe():
        while not stop.is_set():
            try:
                frame = listener.recv(timeout=.1)
                if frame is not None and not frame.is_extended_id and frame.arbitration_id in ids:
                    bus_rows.append((frame.timestamp, frame.arbitration_id, bytes(frame.data).hex(), frame.is_rx))
            except Exception as exc:
                errors.append(f'监听器错误：{exc}')
                stop.set()
                return

    try:
        if args.listener_interface:
            listener = Bus(interface=args.listener_interface, channel=args.listener_channel)
        backend = open_backend(0, device_id=args.device_id) if args.device_id else open_backend(0)
        backend.open_channel(args.channel)
        if listener:
            observer = threading.Thread(target=observe, daemon=True)
            observer.start()
        for can_id, period in zip(ids, periods):
            frame = Message(channel=args.channel, arbitration_id=can_id, is_extended_id=False,
                            is_fd=args.fd, bitrate_switch=args.fd and args.brs,
                            data=bytes(8), check=True)
            worker = PeriodicSend(frame, period * 1_000_000, dispatch, logger)
            workers.append(worker)
            worker.start()
        start = time.monotonic()
        updated = False
        while not stop.wait(.1) and time.monotonic() - start < args.duration:
            if args.scenario == 'update' and not updated and time.monotonic() - start >= args.duration / 2:
                frame = Message(channel=args.channel, arbitration_id=ids[0], is_extended_id=False,
                                is_fd=args.fd, bitrate_switch=args.fd and args.brs,
                                data=bytes([1] + [0] * 7), check=True)
                with io_lock:
                    if not workers[0].update(frame, periods[0] * 1_000_000):
                        errors.append('更新失败：任务已停止')
                updated = True
            if any(w.error for w in workers):
                errors.extend(w.error for w in workers if w.error)
                break
    finally:
        stop.set()
        for worker in workers: worker.cancel()
        for worker in workers: worker.join()
        if observer: observer.join()
        if listener:
            try: listener.shutdown()
            except Exception as exc: errors.append(f'监听器关闭失败：{exc}')
        if backend:
            try: backend.close()
            except Exception as exc: errors.append(f'设备关闭失败：{exc}')
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output / 'host_calls.csv').open('w', newline='') as f:
            writer = csv.writer(f); writer.writerow(['start_unix_ns', 'end_unix_ns', 'id', 'data', 'deadline_lateness_ns']); writer.writerows(host_rows)
        with (args.output / 'listener.csv').open('w', newline='') as f:
            writer = csv.writer(f); writer.writerow(['listener_timestamp_s', 'id', 'data', 'is_rx']); writer.writerows(bus_rows)
        summary = {'scenario': args.scenario, 'requested_duration_s': args.duration,
                   'host_call_count': len(host_rows), 'listener_frame_count': len(bus_rows),
                   'timestamp_note': 'host calls are not bus timestamps; verify listener timestamp provenance',
                   'lost_frames': None, 'errors': errors, 'ids': {}}
        for can_id, period in zip(ids, periods):
            stamps = sorted(row[0] for row in bus_rows if row[1] == can_id)
            deviations = [abs((b-a)*1000-period) for a,b in zip(stamps, stamps[1:])]
            summary['ids'][hex(can_id)] = {'period_ms': period, 'listener_intervals': len(deviations),
                                           'absolute_deviation_ms': stats(deviations)}
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    if errors:
        raise RuntimeError('; '.join(str(x) for x in errors))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--confirm-safe', action='store_true')
    p.add_argument('--backend', choices=['simulation', 'hardware', 'mac-usb', 'hirain'], required=True)
    p.add_argument('--device-id')
    p.add_argument('--channel', type=int, default=0)
    p.add_argument('--scenario', choices=SCENARIOS, required=True)
    p.add_argument('--duration', type=float, default=60)
    p.add_argument('--base-id', type=lambda x: int(x, 0), required=True)
    p.add_argument('--fd', action='store_true')
    p.add_argument('--brs', action='store_true')
    p.add_argument('--listener-interface')
    p.add_argument('--listener-channel')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(args), indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
