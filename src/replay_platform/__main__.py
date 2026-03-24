from __future__ import annotations

from pathlib import Path

from replay_platform.app_controller import ReplayApplication


def main() -> None:
    try:
        from replay_platform.ui.qt_app import run_qt_app
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "未安装 PySide6。请先在 Windows 环境安装项目依赖后再启动。"
        ) from exc
    workspace = Path.cwd()
    app = ReplayApplication(workspace)
    run_qt_app(app)


if __name__ == "__main__":
    main()
