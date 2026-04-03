from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
import time
from typing import List, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replay_platform.adapters.zlg import ZlgDeviceAdapter  # noqa: E402
from replay_platform.core import (  # noqa: E402
    BusType,
    DeviceChannelBinding,
    FrameEvent,
    ReplayFrameLogMode,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayState,
    ReplayStats,
    ScenarioSpec,
)
from replay_platform.runtime.engine import ReplayEngine  # noqa: E402
from replay_platform.services.signal_catalog import SignalOverrideService  # noqa: E402
from replay_platform.services.trace_loader import TraceLoader  # noqa: E402


DEFAULT_TRACE = ROOT / ".replay_platform" / "traces" / "c3c4dccb51e34877a8a8295facc89af6.asc"
STARTUP_WARNING_WINDOW_MS = 200.0
PARTIAL_SEND_MARKERS = ("回放帧发送未完成", "鍥炴斁甯у彂閫佹湭瀹屾垚")
FRAME_LOG_MARKERS = ("回放帧[", "鍥炴斁甯?[")
ERROR_MARKERS = ("异常", "失败", "寮傚父", "澶辫触")


@dataclass
class TimedMessage:
    elapsed_ms: float
    message: str


@dataclass
class RunResult:
    tx_echo_enabled: bool
    merge_receive_enabled: bool
    duration_ms: float
    stats: ReplayStats
    warning_logs: List[TimedMessage]
    error_logs: List[TimedMessage]
    info_logs: List[TimedMessage]
    frame_log_count: int
    frame_log_samples: List[TimedMessage]
    tx_echo_events: List[FrameEvent]
    tx_echo_read_errors: List[str]
    timed_out: bool


class RunLogger:
    def __init__(self, replay_start_perf_ns: int, *, verbose: bool = False, frame_sample_limit: int = 12) -> None:
        self._replay_start_perf_ns = replay_start_perf_ns
        self._verbose = verbose
        self._frame_sample_limit = frame_sample_limit
        self.warning_logs: List[TimedMessage] = []
        self.error_logs: List[TimedMessage] = []
        self.info_logs: List[TimedMessage] = []
        self.frame_log_count = 0
        self.frame_log_samples: List[TimedMessage] = []

    def __call__(self, message: str) -> None:
        elapsed_ms = (time.perf_counter_ns() - self._replay_start_perf_ns) / 1_000_000
        record = TimedMessage(elapsed_ms=elapsed_ms, message=message)
        normalized = message.lower()
        if any(marker in message for marker in PARTIAL_SEND_MARKERS) or "warning" in normalized:
            self.warning_logs.append(record)
        elif any(marker in message for marker in ERROR_MARKERS):
            self.error_logs.append(record)
        else:
            self.info_logs.append(record)
        if "ID=0x" in message and any(marker in message for marker in FRAME_LOG_MARKERS):
            self.frame_log_count += 1
            if len(self.frame_log_samples) < self._frame_sample_limit:
                self.frame_log_samples.append(record)
        if self._verbose:
            print(f"[{elapsed_ms:9.3f} ms] {message}")


class TxEchoReader:
    def __init__(
        self,
        adapter: ZlgDeviceAdapter,
        *,
        physical_channel: int,
        bus_type: BusType,
        poll_interval_s: float = 0.001,
        post_stop_idle_polls: int = 30,
    ) -> None:
        self._adapter = adapter
        self._physical_channel = physical_channel
        self._bus_type = bus_type
        self._poll_interval_s = poll_interval_s
        self._post_stop_idle_polls = post_stop_idle_polls
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: List[FrameEvent] = []
        self._errors: List[str] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tx-echo-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @property
    def events(self) -> List[FrameEvent]:
        with self._lock:
            return list(self._events)

    @property
    def errors(self) -> List[str]:
        with self._lock:
            return list(self._errors)

    def _run(self) -> None:
        idle_polls = 0
        while True:
            try:
                batch = self._adapter.read(limit=512, timeout_ms=0)
            except Exception as exc:  # pragma: no cover - hardware polling fallback
                if self._stop_requested.is_set():
                    break
                with self._lock:
                    self._errors.append(str(exc))
                time.sleep(self._poll_interval_s)
                continue
            filtered = [
                item
                for item in batch
                if item.channel == self._physical_channel
                and item.bus_type == self._bus_type
                and str(item.flags.get("direction", "")).lower() == "tx"
            ]
            if filtered:
                with self._lock:
                    self._events.extend(filtered)
                idle_polls = 0
            elif self._stop_requested.is_set():
                idle_polls += 1
                if idle_polls >= self._post_stop_idle_polls:
                    break
            else:
                idle_polls = 0
            time.sleep(self._poll_interval_s)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ZLG USBCANFD-200U CH1 CANFD 起播丢帧排查脚本")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE, help="待回放 ASC 文件路径")
    parser.add_argument("--logical-channel", type=int, default=1, help="trace 逻辑通道")
    parser.add_argument("--physical-channel", type=int, default=1, help="ZLG 物理通道")
    parser.add_argument("--device-type", default="USBCANFD_200U", help="ZLG 设备类型常量名")
    parser.add_argument("--device-index", type=int, default=0, help="ZLG 设备索引")
    parser.add_argument("--sdk-root", type=Path, default=ROOT / "zlgcan_python_251211", help="ZLG SDK 根目录")
    parser.add_argument("--nominal-baud", type=int, default=500000, help="CANFD 仲裁域波特率")
    parser.add_argument("--data-baud", type=int, default=2000000, help="CANFD 数据域波特率")
    parser.add_argument("--window-ms", type=float, default=20.0, help="用于时序对比的起播窗口")
    parser.add_argument("--max-wait-ms", type=float, default=8000.0, help="单轮回放最长等待时间")
    parser.add_argument("--round-retry-count", type=int, default=3, help="单轮设备打开失败时的重试次数")
    parser.add_argument("--round-retry-delay-ms", type=float, default=400.0, help="单轮设备打开失败后的重试等待")
    parser.add_argument("--verbose", action="store_true", help="实时打印日志")
    return parser.parse_args()


def clone_events_for_round(events: Sequence[FrameEvent], *, tx_echo: bool) -> List[FrameEvent]:
    cloned: List[FrameEvent] = []
    for item in events:
        flags = dict(item.flags)
        if tx_echo:
            flags["tx_echo"] = True
        cloned.append(item.clone(flags=flags))
    return cloned


def build_binding(args: argparse.Namespace, *, tx_echo: bool) -> DeviceChannelBinding:
    return DeviceChannelBinding(
        adapter_id="zlg0",
        driver="zlg",
        logical_channel=args.logical_channel,
        physical_channel=args.physical_channel,
        bus_type=BusType.CANFD,
        device_type=args.device_type,
        device_index=args.device_index,
        sdk_root=str(args.sdk_root),
        nominal_baud=args.nominal_baud,
        data_baud=args.data_baud,
        resistance_enabled=True,
        listen_only=False,
        tx_echo=tx_echo,
        merge_receive=False,
    )


def clone_binding_with_receive(binding: DeviceChannelBinding, *, merge_receive: bool) -> DeviceChannelBinding:
    return DeviceChannelBinding(
        adapter_id=binding.adapter_id,
        driver=binding.driver,
        logical_channel=binding.logical_channel,
        physical_channel=binding.physical_channel,
        bus_type=binding.bus_type,
        device_type=binding.device_type,
        device_index=binding.device_index,
        sdk_root=binding.sdk_root,
        nominal_baud=binding.nominal_baud,
        data_baud=binding.data_baud,
        resistance_enabled=binding.resistance_enabled,
        listen_only=binding.listen_only,
        tx_echo=binding.tx_echo,
        merge_receive=merge_receive,
        network=dict(binding.network),
        metadata=dict(binding.metadata),
    )


def build_scenario(binding: DeviceChannelBinding) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id="zlg-startup-diagnose",
        name="ZLG CH1 CANFD 起播诊断",
        bindings=[binding],
    )


def copy_stats(stats: ReplayStats) -> ReplayStats:
    return ReplayStats(
        sent_frames=stats.sent_frames,
        skipped_frames=stats.skipped_frames,
        diagnostic_actions=stats.diagnostic_actions,
        link_actions=stats.link_actions,
        errors=list(stats.errors),
    )


def load_trace(path: Path, logical_channel: int) -> List[FrameEvent]:
    events = TraceLoader().load(str(path))
    filtered = [item for item in events if item.channel == logical_channel and item.bus_type == BusType.CANFD]
    if not filtered:
        raise SystemExit(f"未在 {path} 中找到逻辑通道 {logical_channel} 的 CANFD 帧。")
    return filtered


def run_round(
    args: argparse.Namespace,
    events: Sequence[FrameEvent],
    *,
    tx_echo: bool,
    merge_receive: bool = False,
) -> RunResult:
    round_events = clone_events_for_round(events, tx_echo=tx_echo)
    binding = clone_binding_with_receive(build_binding(args, tx_echo=tx_echo), merge_receive=merge_receive)
    scenario = build_scenario(binding)
    adapter = ZlgDeviceAdapter(binding.adapter_id, binding)
    replay_start_perf_ns = time.perf_counter_ns()
    logger = RunLogger(replay_start_perf_ns, verbose=args.verbose)
    engine = ReplayEngine(
        signal_overrides=SignalOverrideService(),
        logger=logger,
        log_config=ReplayLogConfig(
            level=ReplayLogLevel.DEBUG,
            frame_mode=ReplayFrameLogMode.ALL,
        ),
    )
    engine.configure(scenario, round_events, {binding.adapter_id: adapter}, {})
    reader = TxEchoReader(adapter, physical_channel=binding.physical_channel, bus_type=BusType.CANFD) if tx_echo else None
    timed_out = False
    started_perf_ns = time.perf_counter_ns()
    try:
        engine.start()
        if reader is not None:
            reader.start()
        deadline = started_perf_ns + int(args.max_wait_ms * 1_000_000)
        while engine.state != ReplayState.STOPPED:
            if time.perf_counter_ns() >= deadline:
                timed_out = True
                engine.stop()
                break
            time.sleep(0.005)
    finally:
        if reader is not None:
            reader.stop()
        if engine.state != ReplayState.STOPPED:
            engine.stop()
    duration_ms = (time.perf_counter_ns() - started_perf_ns) / 1_000_000
    return RunResult(
        tx_echo_enabled=tx_echo,
        merge_receive_enabled=merge_receive,
        duration_ms=duration_ms,
        stats=copy_stats(engine.stats),
        warning_logs=list(logger.warning_logs),
        error_logs=list(logger.error_logs),
        info_logs=list(logger.info_logs),
        frame_log_count=logger.frame_log_count,
        frame_log_samples=list(logger.frame_log_samples),
        tx_echo_events=reader.events if reader is not None else [],
        tx_echo_read_errors=reader.errors if reader is not None else [],
        timed_out=timed_out,
    )


def run_round_with_retries(
    args: argparse.Namespace,
    events: Sequence[FrameEvent],
    *,
    tx_echo: bool,
    merge_receive: bool = False,
) -> RunResult:
    last_error: Exception | None = None
    attempts = max(1, args.round_retry_count)
    for attempt in range(1, attempts + 1):
        try:
            return run_round(args, events, tx_echo=tx_echo, merge_receive=merge_receive)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            print(
                f"第 {attempt} 次{'Tx echo' if tx_echo else '基线'}回放启动失败: {exc}，"
                f"等待 {args.round_retry_delay_ms:.0f}ms 后重试..."
            )
            time.sleep(args.round_retry_delay_ms / 1_000)
    assert last_error is not None
    raise last_error


def relative_window(events: Sequence[FrameEvent], window_ns: int) -> List[FrameEvent]:
    if not events:
        return []
    start_ns = events[0].ts_ns
    return [item for item in events if item.ts_ns - start_ns <= window_ns]


def gap_us(events: Sequence[FrameEvent]) -> List[int]:
    return [int((events[index].ts_ns - events[index - 1].ts_ns) / 1_000) for index in range(1, len(events))]


def first_values(values: Sequence[int], *, limit: int = 12) -> str:
    if not values:
        return "-"
    preview = ", ".join(str(item) for item in values[:limit])
    if len(values) > limit:
        preview += ", ..."
    return preview


def count_startup_warnings(warnings: Sequence[TimedMessage], *, window_ms: float = STARTUP_WARNING_WINDOW_MS) -> int:
    return sum(1 for item in warnings if item.elapsed_ms <= window_ms)


def first_span_us(events: Sequence[FrameEvent], count: int) -> int | None:
    if len(events) < count:
        return None
    return int((events[count - 1].ts_ns - events[0].ts_ns) / 1_000)


def classify_partial_send(run: RunResult) -> tuple[bool, str]:
    startup_warning_count = count_startup_warnings(run.warning_logs)
    if run.stats.skipped_frames <= 0 or not run.warning_logs:
        return False, "未观察到 skipped + partial send 告警组合。"
    if startup_warning_count == len(run.warning_logs):
        return True, "所有 partial send 告警都集中在起播 200ms 内。"
    if startup_warning_count > 0:
        return True, f"{startup_warning_count}/{len(run.warning_logs)} 条 partial send 告警集中在起播 200ms 内。"
    return False, "partial send 告警未集中在起播窗口，更像整体发送能力不足。"


def classify_delay_granularity(expected_gaps: Sequence[int], actual_gaps: Sequence[int]) -> tuple[bool, str]:
    expected_sub_ms = [item for item in expected_gaps if 100 <= item < 1000]
    if len(expected_sub_ms) < 3 or not actual_gaps:
        return False, "样本不足，无法判断亚毫秒延时是否被压扁。"
    actual_zeroish = sum(1 for item in actual_gaps if item <= 50)
    actual_onems = sum(1 for item in actual_gaps if 800 <= item <= 1200)
    actual_sub_ms = sum(1 for item in actual_gaps if 100 <= item < 1000)
    suspicious = (actual_zeroish + actual_onems) >= 3 and (actual_zeroish + actual_onems) > actual_sub_ms
    if suspicious:
        return True, f"实际 Tx echo 间隔中 ~0ms 和 ~1ms 桶共 {actual_zeroish + actual_onems} 个，超过保留下来的亚毫秒间隔 {actual_sub_ms} 个。"
    return False, f"实际 Tx echo 仍保留了 {actual_sub_ms} 个亚毫秒间隔，~0ms/~1ms 桶共 {actual_zeroish + actual_onems} 个。"


def classify_delay_send_effect(expected_window: Sequence[FrameEvent], actual_window: Sequence[FrameEvent]) -> tuple[bool, str]:
    expected_span = first_span_us(expected_window, 5)
    actual_span = first_span_us(actual_window, 5)
    if expected_span is None or actual_span is None:
        return False, "前 5 帧样本不足，无法判断 delay-send 是否退化成突发发送。"
    if expected_span >= 2000 and actual_span <= max(int(expected_span * 0.2), 500):
        return True, f"期望前 5 帧跨度为 {expected_span}us，实际 Tx echo 仅 {actual_span}us，明显更像突发发送。"
    return False, f"期望前 5 帧跨度 {expected_span}us，实际 Tx echo 为 {actual_span}us。"


def print_trace_summary(events: Sequence[FrameEvent], *, window_ns: int) -> None:
    window = relative_window(events, window_ns)
    gaps = gap_us(window)
    print("== Trace 基线 ==")
    print(f"帧数: {len(events)}")
    print(f"首帧时间戳: {events[0].ts_ns / 1_000_000:.3f} ms")
    print(f"起播 {window_ns / 1_000_000:.1f} ms 窗口帧数: {len(window)}")
    print(f"起播窗口前 12 个相邻间隔(us): {first_values(gaps)}")
    print()


def print_run_summary(label: str, run: RunResult) -> None:
    startup_warning_count = count_startup_warnings(run.warning_logs)
    print(f"== {label} ==")
    print(f"tx_echo: {'on' if run.tx_echo_enabled else 'off'}")
    print(f"merge_receive: {'on' if run.merge_receive_enabled else 'off'}")
    print(f"耗时: {run.duration_ms:.3f} ms{' (超时强停)' if run.timed_out else ''}")
    print(
        "统计: sent={sent} skipped={skipped} diag={diag} link={link} errors={errors}".format(
            sent=run.stats.sent_frames,
            skipped=run.stats.skipped_frames,
            diag=run.stats.diagnostic_actions,
            link=run.stats.link_actions,
            errors=len(run.stats.errors),
        )
    )
    print(f"逐帧 debug 日志条数: {run.frame_log_count}")
    print(f"partial send 告警数: {len(run.warning_logs)} (起播 {STARTUP_WARNING_WINDOW_MS:.0f}ms 内 {startup_warning_count} 条)")
    if run.warning_logs:
        warning_preview = ", ".join(f"{item.elapsed_ms:.3f}ms" for item in run.warning_logs[:8])
        if len(run.warning_logs) > 8:
            warning_preview += ", ..."
        print(f"partial send 告警时刻: {warning_preview}")
    if run.stats.errors:
        print("运行时错误:")
        for message in run.stats.errors:
            print(f"  - {message}")
    if run.tx_echo_read_errors:
        print("Tx echo 读回异常:")
        for message in run.tx_echo_read_errors[:5]:
            print(f"  - {message}")
        if len(run.tx_echo_read_errors) > 5:
            print(f"  - ... 共 {len(run.tx_echo_read_errors)} 条")
    print()


def print_echo_analysis(
    expected_events: Sequence[FrameEvent],
    run: RunResult,
    *,
    window_ns: int,
) -> None:
    expected_window = relative_window(expected_events, window_ns)
    actual_window = relative_window(run.tx_echo_events, window_ns)
    expected_gaps = gap_us(expected_window)
    actual_gaps = gap_us(actual_window)
    partial_send_flag, partial_send_reason = classify_partial_send(run)
    granularity_flag, granularity_reason = classify_delay_granularity(expected_gaps, actual_gaps)
    delay_send_flag, delay_send_reason = classify_delay_send_effect(expected_window, actual_window)
    print("== Tx echo 时序对比 ==")
    print(f"回读 Tx echo 总数: {len(run.tx_echo_events)}")
    print(f"起播 {window_ns / 1_000_000:.1f} ms 窗口 Tx echo 数: {len(actual_window)}")
    print(f"期望起播窗口前 12 个相邻间隔(us): {first_values(expected_gaps)}")
    print(f"实际 Tx echo 前 12 个相邻间隔(us): {first_values(actual_gaps)}")
    expected_span = first_span_us(expected_window, 5)
    actual_span = first_span_us(actual_window, 5)
    print(
        "前 5 帧跨度(us): 期望={expected} 实际={actual}".format(
            expected=expected_span if expected_span is not None else "-",
            actual=actual_span if actual_span is not None else "-",
        )
    )
    print()
    print("== 诊断结论 ==")
    print(f"1. 起播预排队 partial send: {'成立' if partial_send_flag else '未成立'}")
    print(f"   {partial_send_reason}")
    print(f"2. queue delay 粒度压扁: {'成立' if granularity_flag else '未成立'}")
    print(f"   {granularity_reason}")
    print(f"3. delay-send 退化成突发: {'成立' if delay_send_flag else '未成立'}")
    print(f"   {delay_send_reason}")
    print()


def verify_inputs(trace_path: Path, sdk_root: Path) -> None:
    if not trace_path.exists():
        raise SystemExit(f"trace 不存在: {trace_path}")
    if not sdk_root.exists():
        raise SystemExit(f"sdk_root 不存在: {sdk_root}")


def main() -> int:
    args = parse_args()
    verify_inputs(args.trace, args.sdk_root)
    window_ns = int(args.window_ms * 1_000_000)
    trace_events = load_trace(args.trace, args.logical_channel)
    print("ZLG USBCANFD-200U CH1 CANFD 起播排查")
    print(f"trace: {args.trace}")
    print(f"sdk_root: {args.sdk_root}")
    print(f"逻辑通道={args.logical_channel} 物理通道={args.physical_channel} 波特率={args.nominal_baud}/{args.data_baud}")
    print()
    print_trace_summary(trace_events, window_ns=window_ns)
    baseline = run_round_with_retries(args, trace_events, tx_echo=False, merge_receive=False)
    print_run_summary("第一轮基线回放", baseline)
    time.sleep(args.round_retry_delay_ms / 1_000)
    echo_run = run_round_with_retries(args, trace_events, tx_echo=True, merge_receive=False)
    print_run_summary("第二轮 Tx echo 回放", echo_run)
    analysis_run = echo_run
    if not echo_run.tx_echo_events:
        print("未通过普通接收路径读到 Tx echo，追加一次 merge_receive=true 的 Tx echo 验证。")
        print()
        time.sleep(args.round_retry_delay_ms / 1_000)
        merge_echo_run = run_round_with_retries(args, trace_events, tx_echo=True, merge_receive=True)
        print_run_summary("第三轮 Tx echo 回放（merge_receive=true）", merge_echo_run)
        analysis_run = merge_echo_run
    print_echo_analysis(trace_events, analysis_run, window_ns=window_ns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
