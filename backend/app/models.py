"""后端数据模型；不包含 UI 风格定义。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class MetricsConfig(BaseModel):
    """可独立启用的采样模块。"""

    cpu: bool = True
    memory: bool = True
    fps: bool = True

    def enabled_names(self) -> list[str]:
        return [name for name, enabled in self.model_dump().items() if enabled]


class StartSessionRequest(BaseModel):
    """创建会话的受限请求体，最长持续 60 分钟。"""

    serial: str = Field(min_length=1, max_length=128)
    duration_seconds: int = Field(default=900, ge=1, le=3600)
    interval_ms: int = Field(default=1000, ge=500, le=5000)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    surface_layer: str | None = Field(default=None, max_length=256)
    # 内存采样独立降频：每 N 个采样周期采一次内存（dumpsys meminfo 全量
    # 在部分车机上耗时数秒，会拖慢整个采样周期；内存变化缓慢，降频不损失
    # 趋势信息）。默认 5：0.5s 间隔下约 2.5s 一个内存点。
    memory_cycle_skip: int = Field(default=5, ge=1, le=60)
    # 设备日志导出：留空表示不导出该类型（仅前端提示）
    anr_path: str | None = Field(default=None, max_length=256)
    crash_path: str | None = Field(default=None, max_length=256)
    tombstone_path: str | None = Field(default=None, max_length=256)
    log_export_root: str | None = Field(default=None, max_length=512)

    @field_validator("serial")
    @classmethod
    def serial_is_safe(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
        if any(character not in allowed for character in value):
            raise ValueError("ADB 序列号包含不允许的字符")
        return value

    @model_validator(mode="after")
    def has_enabled_metric(self) -> "StartSessionRequest":
        if not self.metrics.enabled_names():
            raise ValueError("至少需要启用一项测试")
        return self

    def log_export_enabled(self) -> bool:
        return any((self.anr_path, self.crash_path, self.tombstone_path))


@dataclass(slots=True)
class ProcessSample:
    """单个进程的瞬时资源占用；所有内存字段以 KiB 保存。"""

    process_name: str
    pid: int | None = None
    cpu_pct: float | None = None
    pss_kb: int | None = None
    rss_kb: int | None = None


@dataclass(slots=True)
class SamplePayload:
    """一个采样周期的一组一级指标和进程明细。"""

    ts_ms: int
    cpu_total_pct: float | None = None
    pss_kb: int | None = None
    rss_kb: int | None = None
    total_ram_kb: int | None = None
    fps: float | None = None
    app_render_fps: float | None = None
    app_jank_pct: float | None = None
    # 逐帧统计（来自 FrameTimeline / framestats / SF latency 中最优可用源）
    frame_source: str | None = None
    frame_count: int | None = None
    jank_count: int | None = None
    jank_pct: float | None = None
    avg_frame_time_ms: float | None = None
    p95_frame_time_ms: float | None = None
    p99_frame_time_ms: float | None = None
    input_latency_ms: float | None = None
    statuses: dict[str, str] | None = None
    processes: list[ProcessSample] | None = None

    def serializable(self) -> dict[str, Any]:
        result = asdict(self)
        result["processes"] = [asdict(item) for item in self.processes or []]
        return result

