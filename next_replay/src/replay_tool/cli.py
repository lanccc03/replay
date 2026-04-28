from __future__ import annotations

import argparse
from pathlib import Path
import sys

from replay_tool.app import ReplayApplication
from replay_tool.domain import DeviceConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replay-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate and compile a scenario.")
    validate_parser.add_argument("scenario")

    run_parser = subparsers.add_parser("run", help="Run a scenario.")
    run_parser.add_argument("scenario")

    devices_parser = subparsers.add_parser("devices", help="List device channels.")
    devices_parser.add_argument("--driver", default="tongxing")
    devices_parser.add_argument("--sdk-root", default="../TSMaster/Windows")
    devices_parser.add_argument("--application", default="ReplayTool")
    devices_parser.add_argument("--device-type", default="TC1014")
    devices_parser.add_argument("--device-index", type=int, default=0)

    args = parser.parse_args(argv)
    app = ReplayApplication(logger=print)
    try:
        if args.command == "validate":
            plan = app.validate(args.scenario)
            print(f"OK: {plan.name} frames={len(plan.frames)} devices={len(plan.devices)} channels={len(plan.channels)}")
            return 0
        if args.command == "run":
            runtime = app.run(args.scenario)
            snapshot = runtime.snapshot()
            print(
                "DONE: state={state} sent={sent} skipped={skipped} errors={errors}".format(
                    state=snapshot.state.value,
                    sent=snapshot.sent_frames,
                    skipped=snapshot.skipped_frames,
                    errors=len(snapshot.errors),
                )
            )
            return 0 if not snapshot.errors else 2
        if args.command == "devices":
            config = DeviceConfig(
                id="device0",
                driver=args.driver,
                application=args.application,
                sdk_root=str(Path(args.sdk_root)),
                device_type=args.device_type,
                device_index=args.device_index,
            )
            device = app.create_device(config)
            info = device.open()
            channels = device.enumerate_channels()
            print(f"{info.driver}:{info.name} serial={info.serial_number} channels={list(channels)}")
            device.close()
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
