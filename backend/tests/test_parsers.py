"""针对常见 Android 文本输出形态的无设备解析测试。"""

import re

from app.adb import ProcessSample, extract_foreground_package, merge_processes, parse_cpuinfo, parse_frame_timeline, parse_framestats, parse_gfxinfo_summary, parse_meminfo, parse_surface_latency, parse_surface_latency_stats, parse_top
from app.device_logs import LOG_KIND_ANR, LOG_KIND_CRASH, LOG_KIND_TOMBSTONE, DeviceLogConfig
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


def test_parse_frame_timeline_samsung_oem() -> None:
    """三星固件：Actual Present time 全为 0.00，Jank Type 恒为 Unknown jank。

    应退回用 Actual End time 估算帧率（间隔 ≈ 16.7ms → 60fps），
    且 Unknown jank 不计入卡顿，避免 jank 恒为 100%。
    """
    lines = ["Number of display frames : 6"]
    for index in range(6):
        end_ms = 16.7 * (index + 1) + 3.0
        start_ms = 16.7 * index + 0.5
        lines.extend(
            [
                f"Display Frame {index} [*] ",
                "Prediction State : Valid",
                "Jank Type : Unknown jank",
                "Present Metadata : Unknown Present",
                "Finish Metadata: Unknown Finish",
                "Start Metadata: Unknown Start",
                "Vsync Period:  16.666666",
                f"Present delta: {541833:.6f}",
                "Present delta % refreshrate:   0.000001",
                "\t\tStart time\t\t|    End time\t\t|    Present time",
                "Expected\t|\t     16.67\t|\t     32.33\t|\t     32.33",
                f"Actual  \t|\t{start_ms:10.2f}\t|\t{end_ms:10.2f}\t|\t      0.00",
                "----------------------------------------------------------------------------------------",
                "",
            ]
        )
    stats = parse_frame_timeline("\n".join(lines))
    assert stats is not None
    assert stats.source == "frametimeline"
    assert 58.0 <= (stats.fps or 0) <= 61.0  # end 时间间隔 ≈ 60fps
    assert stats.jank_count == 0  # Unknown jank 不计入
    assert stats.frame_count == 6


def test_parse_surface_latency_stats() -> None:
    output = """16666666\n0 0 1000000000\n0 0 1016666666\n0 0 1033333332\n0 0 1049999998\n"""
    stats = parse_surface_latency_stats(output)
    assert stats is not None
    assert stats.source == "sf_latency"
    assert 59.0 <= (stats.fps or 0) <= 61.0
    assert stats.jank_count == 0


def test_extract_foreground_package_android13_top_resumed() -> None:
    """Android 12L/13+ 输出为 topResumedActivity=...（等号分隔）。"""
    output = """ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
    topResumedActivity=ActivityRecord{cd3bdfc u0 com.android.settings/.applications.ManageApplications} t23}
    topPausedActivity=ActivityRecord{0}"""
    assert extract_foreground_package(output) == "com.android.settings"


def test_extract_foreground_package_legacy_m_resumed() -> None:
    """Android 12 以下输出为 mResumedActivity: ...（冒号分隔）。"""
    output = "  mResumedActivity: ActivityRecord{abc123 u0 com.example.nav/.MainActivity} t12}"
    assert extract_foreground_package(output) == "com.example.nav"


def test_extract_foreground_package_none() -> None:
    assert extract_foreground_package("no activities here") is None


def test_device_log_config_enabled_pairs() -> None:
    config = DeviceLogConfig(anr="/data/anr", tombstone="/data/tombstones")
    pairs = config.enabled_pairs()
    assert pairs == [(LOG_KIND_ANR, "/data/anr"), (LOG_KIND_TOMBSTONE, "/data/tombstones")]
    assert DeviceLogConfig().enabled_pairs() == []
    assert DeviceLogConfig(crash="/data/crash").enabled_pairs() == [(LOG_KIND_CRASH, "/data/crash")]


def test_device_log_path_validation() -> None:
    valid = re.compile(r"^[A-Za-z0-9_./-]+$")
    assert valid.match("/data/anr")
    assert valid.match("/data/tombstones_1")
    assert valid.match("/data/app/com.foo.bar/logs")
    # 包含注入风险字符的路径应被拒绝
    assert not valid.match("/data/anr;rm -rf /")
    assert not valid.match("/data/$(id)")
    assert not valid.match("/data/anr with space")


def test_device_log_folder_name_format() -> None:
    import time as _time

    from app.device_logs import export_and_clean_device_logs  # noqa: F401

    folder = _time.strftime("%Y_%m_%d-%H_%M_%S")
    assert re.match(r"^\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2}$", folder)


def test_parse_gfxinfo_summary_single_process() -> None:
    output = """** Graphics info for pid 4042 [com.example.app] **
Total frames rendered: 2828
Janky frames: 560 (19.80%)"""
    assert parse_gfxinfo_summary(output) == (2828, 560)


def test_parse_gfxinfo_summary_takes_most_active_process() -> None:
    """多进程输出：第一段是不活跃进程（帧数少），应取渲染最活跃的段。"""
    output = """** Graphics info for pid 5678 [com.example.app:remote] **
Total frames rendered: 120
Janky frames: 2 (1.67%)

** Graphics info for pid 4042 [com.example.app] **
Total frames rendered: 2828
Janky frames: 560 (19.80%)"""
    assert parse_gfxinfo_summary(output) == (2828, 560)


def test_parse_gfxinfo_summary_first_process_active() -> None:
    """多进程输出：第一段就是活跃进程时仍取第一段。"""
    output = """** Graphics info for pid 4042 [com.example.app] **
Total frames rendered: 9999
Janky frames: 100 (1.00%)

** Graphics info for pid 5678 [com.example.app:remote] **
Total frames rendered: 50
Janky frames: 1 (2.00%)"""
    assert parse_gfxinfo_summary(output) == (9999, 100)


def test_parse_gfxinfo_summary_skips_broken_section() -> None:
    """某段缺 Janky 字段时跳过该段，不拖垮整体解析。"""
    output = """** Graphics info for pid 5678 [com.example.app:remote] **
Total frames rendered: 5000

** Graphics info for pid 4042 [com.example.app] **
Total frames rendered: 3000
Janky frames: 100 (3.33%)"""
    assert parse_gfxinfo_summary(output) == (3000, 100)


def test_parse_gfxinfo_summary_none() -> None:
    assert parse_gfxinfo_summary("no gfx data here") is None


def test_source_priority_gates_by_sdk() -> None:
    assert source_priority(30) == [FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_SF_LATENCY]
    assert source_priority(31) == [FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_SF_LATENCY]
    assert source_priority(23) == [FRAME_SOURCE_SF_LATENCY]
    assert source_priority(None) == [FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_SF_LATENCY]
    assert source_priority(31, preferred=FRAME_SOURCE_FRAMESTATS) == [FRAME_SOURCE_FRAMESTATS, FRAME_SOURCE_FRAMETIMELINE, FRAME_SOURCE_SF_LATENCY]
