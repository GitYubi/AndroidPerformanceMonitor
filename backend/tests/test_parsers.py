"""针对常见 Android 文本输出形态的无设备解析测试。"""

from app.adb import ProcessSample, merge_processes, parse_cpuinfo, parse_frame_timeline, parse_framestats, parse_meminfo, parse_surface_latency, parse_surface_latency_stats, parse_top
from app.frame_sources import FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_SF_LATENCY, source_priority


def test_parse_top_and_cpuinfo() -> None:
    top_output = """Tasks: 320 total, 1 running\n400%cpu 22%user 0%nice 6%sys 372%idle\n  PID USER     PR  NI  VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS\n  123 u0_a45   10 -10 1G 120M 20M S 18%  2.0  00:01.0 com.example.nav\n"""
    total, processes = parse_top(top_output)
    assert total == 7.0
    assert processes[0].process_name == "com.example.nav"
    assert processes[0].cpu_pct == 18.0
    cpuinfo = parse_cpuinfo("  2.8% 222/system_server: 2.1% user + 0.6% kernel")
    assert cpuinfo[0].pid == 222
    assert cpuinfo[0].process_name == "system_server"


def test_parse_meminfo_sections() -> None:
    output = """Total RAM: 2,000,000K (status normal)\nTotal PSS by process:\n   100,000K: com.example.nav (pid 123 / activities)\n    20,000K: surfaceflinger (pid 456)\nTotal RSS by process:\n   140,000K: com.example.nav (pid 123 / activities)\n    40,000K: surfaceflinger (pid 456)\n"""
    pss, rss, total_ram, processes = parse_meminfo(output)
    assert pss == 120000
    assert rss == 180000
    assert total_ram == 2000000
    assert len(processes) == 2


def test_parse_surface_latency() -> None:
    output = """16666666\n0 0 1000000000\n0 0 1016666666\n0 0 1033333332\n0 0 1049999998\n"""
    fps = parse_surface_latency(output)
    assert fps is not None
    assert 59.0 <= fps <= 61.0


def test_merge_processes_combines_cpu_and_memory() -> None:
    merged = merge_processes(
        [ProcessSample(process_name="com.example.nav", pid=123, cpu_pct=11.5)],
        [ProcessSample(process_name="com.example.nav", pid=123, pss_kb=100000, rss_kb=140000)],
    )
    assert len(merged) == 1
    assert merged[0].cpu_pct == 11.5
    assert merged[0].pss_kb == 100000


def _framestats_row(intended_ns: int, completed_ns: int, present_ns: int, newest_input_ns: int, handle_input_ns: int, interval_ns: int = 16666666) -> str:
    return ",".join(
        str(value)
        for value in [0, intended_ns, intended_ns + 1000000, newest_input_ns, newest_input_ns, handle_input_ns,
                      handle_input_ns + 1000000, handle_input_ns + 2000000, handle_input_ns + 3000000,
                      handle_input_ns + 4000000, handle_input_ns + 5000000, handle_input_ns + 6000000,
                      handle_input_ns + 7000000, completed_ns, 1000000, 2000000, 3000000,
                      present_ns, present_ns + 1000000, present_ns, interval_ns]
    )


def test_parse_framestats() -> None:
    header = "Flags,IntendedVsync,Vsync,OldestInputEvent,NewestInputEvent,HandleInputStart,AnimationStart,PerformTraversalStart,DrawStart,SyncQueued,SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,DequeueBufferDuration,QueueBufferDuration,GpuCompletedDuration,DisplayPresentTime,CompositionDrawn,FrameDeadline,FrameInterval"
    base = 5_000_000_000
    rows = []
    for index in range(6):
        intended = base + index * 16_666_666
        completed = intended + 12_000_000
        present = intended + 15_000_000
        # 第 4 帧为卡顿帧：耗时 40ms > 2 × 16.67ms
        if index == 3:
            completed = intended + 40_000_000
            present = intended + 43_000_000
        newest_input = intended - 4_000_000
        handle_input = intended - 1_000_000
        rows.append(_framestats_row(intended, completed, present, newest_input, handle_input))
    output = (
        "** Graphics info for pid 12345 [com.example.nav] **\n"
        "Stats since: 123456789ns\n"
        "Total frames rendered: 6\n"
        "Janky frames: 1 (16.67%)\n"
        f"FrameStats:\n    {header}\n"
        + "\n".join(f"    {row}" for row in rows)
    )
    stats = parse_framestats(output)
    assert stats is not None
    assert stats.source == "framestats"
    assert stats.frame_count == 6
    assert stats.jank_count == 1
    assert round(stats.jank_pct or 0, 2) == 16.67
    assert 58.0 <= (stats.fps or 0) <= 61.0
    assert (stats.p95_frame_time_ms or 0) >= 30.0  # 卡顿帧拉高 p95
    assert (stats.input_latency_ms or 0) > 0


def test_parse_framestats_short_rows_older_android() -> None:
    """旧版本无 DisplayPresentTime / FrameInterval 列时仍可解析。"""
    header = "Flags,IntendedVsync,Vsync,OldestInputEvent,NewestInputEvent,HandleInputStart,AnimationStart,PerformTraversalStart,DrawStart,SyncQueued,SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,DequeueBufferDuration,QueueBufferDuration,GpuCompletedDuration"
    base = 8_000_000_000
    rows = []
    for index in range(4):
        intended = base + index * 16_666_666
        rows.append(",".join(str(v) for v in [0, intended, intended + 1000000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, intended + 11_000_000, 1000000, 2000000, 3000000]))
    output = f"FrameStats:\n    {header}\n" + "\n".join(f"    {row}" for row in rows)
    stats = parse_framestats(output)
    assert stats is not None
    assert stats.frame_count == 4
    assert (stats.fps or 0) >= 50.0  # 退回 FrameCompleted 作为呈现时间戳


def test_parse_frame_timeline() -> None:
    lines = ["Number of display frames : 6"]
    for index in range(6):
        present_ms = 16.7 * (index + 1)
        start_ms = 16.7 * index + 0.1
        end_ms = present_ms - 0.4
        jank_line = "Jank Type : AppDeadlineMissed" if index == 4 else "Jank Type : None"
        lines.extend(
            [
                f"Display Frame {index}",
                "Prediction State : Valid",
                jank_line,
                "Present Metadata : OnTimeFinish",
                "Finish Metadata: ",
                "Start Metadata: ",
                "Vsync Period:   16.666666",
                f"Present delta:   {0.1:.6f}",
                f"Present delta % refreshrate:   {0.1:.6f}",
                "\t\tStart time\t\t|    End time\t\t|    Present time",
                f"Expected\t|\t       0.00\t|\t     16.67\t|\t     16.67",
                f"Actual  \t|\t{start_ms:10.2f}\t|\t{end_ms:10.2f}\t|\t{present_ms:10.2f}",
                f"    Layer - com.example.nav#0 [*] " if index == 4 else "    Layer - com.example.nav#0",
                "    Token: 1",
                "    Is Buffer?: 0",
                "    Owner Pid : 12345",
                "    Scheduled rendering rate: 60 fps",
                "    Layer ID : 1",
                "    Present State : Presented",
                "    Prediction State : Valid",
                f"    Jank Type : {'AppDeadlineMissed' if index == 4 else 'None'}",
                "    Present Metadata : OnTimeFinish",
                "    Finish Metadata: ",
                "    Last latch time:   5.000000",
                "    Present delta:   0.500000",
                "\t\tStart time\t\t|    End time\t\t|    Present time",
                "\tExpected\t|\t       0.00\t|\t     16.67\t|\t     16.67",
                f"\tActual  \t|\t{start_ms:10.2f}\t|\t{end_ms:10.2f}\t|\t{present_ms:10.2f}",
                "",
            ]
        )
    stats = parse_frame_timeline("\n".join(lines))
    assert stats is not None
    assert stats.source == "frametimeline"
    assert stats.frame_count == 6
    assert stats.jank_count == 1
    assert 58.0 <= (stats.fps or 0) <= 61.0
    assert stats.refresh_period_ns == 16_666_666


def test_parse_surface_latency_stats() -> None:
    output = """16666666\n0 0 1000000000\n0 0 1016666666\n0 0 1033333332\n0 0 1049999998\n"""
    stats = parse_surface_latency_stats(output)
    assert stats is not None
    assert stats.source == "sf_latency"
    assert 59.0 <= (stats.fps or 0) <= 61.0
    assert stats.jank_count == 0


def test_source_priority_gates_by_sdk() -> None:
    assert source_priority(30) == [FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_SF_LATENCY]
    assert source_priority(31) == [FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_SF_LATENCY]
    assert source_priority(23) == [FRAME_SOURCE_SF_LATENCY]
    assert source_priority(None) == [FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_SF_LATENCY]
    assert source_priority(31, preferred=FRAME_SOURCE_FRAMESTATS) == [FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_SF_LATENCY]
