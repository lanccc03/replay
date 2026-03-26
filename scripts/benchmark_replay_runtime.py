from __future__ import annotations

import tempfile
import time
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replay_platform.adapters.mock import MockDeviceAdapter  # noqa: E402
from replay_platform.core import (  # noqa: E402
    BusType,
    DeviceChannelBinding,
    FrameEvent,
    ReplayFrameLogMode,
    ReplayLogConfig,
    ReplayLogLevel,
    ScenarioSpec,
    SignalOverride,
)
from replay_platform.runtime.engine import ReplayEngine  # noqa: E402
from replay_platform.services.signal_catalog import (  # noqa: E402
    SignalOverrideService,
    StaticMessageCodec,
    StaticMessageDefinition,
)
from replay_platform.services.trace_loader import TraceLoader  # noqa: E402


def run_engine(
    frames: list[FrameEvent],
    *,
    signal_overrides: SignalOverrideService | None = None,
    log_config: ReplayLogConfig | None = None,
) -> float:
    adapter = MockDeviceAdapter("mock-1", channel_count=1)
    scenario = ScenarioSpec(
        scenario_id="bench",
        name="bench",
        bindings=[
            DeviceChannelBinding(
                adapter_id="mock-1",
                driver="mock",
                logical_channel=0,
                physical_channel=0,
                bus_type=frames[0].bus_type if frames else BusType.CAN,
                device_type="MOCK",
            )
        ],
    )
    engine = ReplayEngine(
        signal_overrides=signal_overrides or SignalOverrideService(),
        logger=lambda _message: None,
        log_config=log_config,
    )
    engine.configure(scenario, frames, {"mock-1": adapter}, {})
    started = time.perf_counter()
    engine.start()
    while engine.state.value != "STOPPED":
        time.sleep(0.001)
    return (time.perf_counter() - started) * 1000


def benchmark_override(frame_count: int) -> None:
    frames = [
        FrameEvent(
            ts_ns=0,
            bus_type=BusType.CAN,
            channel=0,
            message_id=0x123,
            payload=b"\x01\x02",
            dlc=2,
        )
        for _ in range(frame_count)
    ]
    overrides = SignalOverrideService()
    overrides.bind_codec(
        0,
        StaticMessageCodec(
            {
                0x123: StaticMessageDefinition(
                    name="VehicleStatus",
                    signal_bytes={"vehicle_speed": 1},
                )
            }
        ),
    )
    for index in range(1000):
        overrides.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x123 + index,
                signal_name="vehicle_speed",
                value=index & 0xFF,
            )
        )
    duration = run_engine(frames, signal_overrides=overrides)
    print(f"override {frame_count} frames -> {duration:.3f}ms")


def benchmark_logging(frame_count: int) -> None:
    frames = [
        FrameEvent(
            ts_ns=0,
            bus_type=BusType.CAN,
            channel=0,
            message_id=0x100 + index,
            payload=b"\x01",
            dlc=1,
        )
        for index in range(frame_count)
    ]
    off = run_engine(frames, log_config=ReplayLogConfig())
    on = run_engine(
        frames,
        log_config=ReplayLogConfig(
            level=ReplayLogLevel.DEBUG,
            frame_mode=ReplayFrameLogMode.ALL,
        ),
    )
    print(f"logging {frame_count} frames -> off {off:.3f}ms / all {on:.3f}ms")


def benchmark_cache(frame_count: int) -> None:
    loader = TraceLoader()
    events = [
        FrameEvent(
            ts_ns=index,
            bus_type=BusType.CAN,
            channel=0,
            message_id=0x123,
            payload=b"\x01\x02\x03\x04",
            dlc=4,
        )
        for index in range(frame_count)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary_path = root / "trace.rplbin"
        json_path = root / "trace.json"
        loader.write_binary_cache(binary_path, events)
        loader.write_cache(json_path, events)
        started = time.perf_counter()
        loader.load_binary_cache(binary_path)
        binary_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        loader.load_cache(json_path)
        json_ms = (time.perf_counter() - started) * 1000
    print(f"cache {frame_count} frames -> binary {binary_ms:.3f}ms / json {json_ms:.3f}ms")


def benchmark_scheduled_send(frame_count: int) -> None:
    frames = [
        FrameEvent(
            ts_ns=5_000_000 + index * 1_000,
            bus_type=BusType.CAN,
            channel=0,
            message_id=0x200 + index,
            payload=b"\x01",
            dlc=1,
        )
        for index in range(frame_count)
    ]
    duration = run_engine(frames)
    print(f"scheduled-send candidate {frame_count} frames -> {duration:.3f}ms")


if __name__ == "__main__":
    benchmark_override(50_000)
    benchmark_override(100_000)
    benchmark_logging(50_000)
    benchmark_logging(100_000)
    benchmark_cache(100_000)
    benchmark_scheduled_send(50_000)
