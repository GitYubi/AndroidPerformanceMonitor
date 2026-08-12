"""针对常见 Android 文本输出形态的无设备解析测试。"""

from app.adb import ProcessSample, merge_processes, parse_cpuinfo, parse_meminfo, parse_surface_latency, parse_top


def test_parse_top_and_cpuinfo() -> None:
    top_output = """Tasks: 320 total, 1 running\n400%cpu 22%user 0%nice 6%sys 372%idle\n  PID USER     PR  NI  VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS\n  123 u0_a45   10 -10 1G 120M 20M S 18%  2.0  00:01.0 com.example.nav\n"""
    total, processes = parse_top(top_output)
    assert total == 28.0
    assert processes[0].process_name == "com.example.nav"
    assert processes[0].cpu_pct == 18.0
    cpuinfo = parse_cpuinfo("  2.8% 222/system_server: 2.1% user + 0.6% kernel")
    assert cpuinfo[0].pid == 222
    assert cpuinfo[0].process_name == "system_server"


def test_parse_meminfo_sections() -> None:
    output = """Total PSS by process:\n   100,000K: com.example.nav (pid 123 / activities)\n    20,000K: surfaceflinger (pid 456)\nTotal RSS by process:\n   140,000K: com.example.nav (pid 123 / activities)\n    40,000K: surfaceflinger (pid 456)\n"""
    pss, rss, processes = parse_meminfo(output)
    assert pss == 120000
    assert rss == 180000
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
