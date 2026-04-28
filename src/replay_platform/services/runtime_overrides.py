from __future__ import annotations

from typing import Callable, Sequence

from replay_platform.core import DatabaseBinding, ScenarioSpec, SignalOverride
from replay_platform.services.signal_catalog import SignalOverrideService


class RuntimeOverrideCoordinator:
    def __init__(
        self,
        *,
        workspace_overrides: Callable[[], list[SignalOverride]],
        log_warning: Callable[[str], None],
    ) -> None:
        self._workspace_overrides = workspace_overrides
        self._log_warning = log_warning

    def load_database_bindings(
        self,
        service: SignalOverrideService,
        bindings: Sequence[DatabaseBinding],
    ) -> dict[int, dict[str, object]]:
        service.clear_codecs()
        statuses: dict[int, dict[str, object]] = {}
        for binding in bindings:
            logical_channel = int(binding.logical_channel)
            normalized_format = str(binding.format or "dbc")
            service.clear_codec(logical_channel)
            status: dict[str, object] = {
                "logical_channel": logical_channel,
                "path": binding.path,
                "format": normalized_format,
                "loaded": False,
                "error": "",
                "message_count": 0,
            }
            try:
                service.load_database(logical_channel, binding.path, format=normalized_format)
            except Exception as exc:
                status["error"] = str(exc).strip() or exc.__class__.__name__
            else:
                status["loaded"] = True
                status["message_count"] = len(service.list_messages(logical_channel))
            statuses[logical_channel] = status
        return statuses

    def validate_signal_overrides(
        self,
        overrides: Sequence[tuple[str, SignalOverride]],
        statuses: dict[int, dict[str, object]],
        service: SignalOverrideService,
    ) -> None:
        errors: list[str] = []
        for source_label, override in overrides:
            logical_channel = int(override.logical_channel)
            status = statuses.get(logical_channel)
            if status is None:
                errors.append(
                    f"{source_label}：LC{logical_channel} 未配置数据库，无法校验报文 0x{override.message_id_or_pgn:X} / {override.signal_name}。"
                )
                continue
            if not status.get("loaded"):
                errors.append(
                    (
                        f"{source_label}：LC{logical_channel} 的数据库 {status.get('path', '')} 加载失败，"
                        f"无法校验报文 0x{override.message_id_or_pgn:X} / {override.signal_name}。"
                        f" 原因：{status.get('error', '未知错误')}"
                    )
                )
                continue
            if override.message_id_or_pgn not in service.list_message_ids(logical_channel):
                errors.append(
                    f"{source_label}：LC{logical_channel} 的数据库未找到报文 0x{override.message_id_or_pgn:X}。"
                )
                continue
            signal_names = service.list_signal_names(logical_channel, override.message_id_or_pgn)
            if override.signal_name not in signal_names:
                errors.append(
                    (
                        f"{source_label}：LC{logical_channel} 报文 0x{override.message_id_or_pgn:X} "
                        f"未找到信号 {override.signal_name}。"
                    )
                )
        if errors:
            raise ValueError("信号覆盖校验失败：\n" + "\n".join(errors))

    def log_database_binding_statuses(self, statuses: dict[int, dict[str, object]]) -> None:
        for logical_channel, status in sorted(statuses.items()):
            if status.get("loaded"):
                continue
            self._log_warning(
                (
                    f"数据库绑定加载失败：LC{logical_channel} 路径={status.get('path', '')} "
                    f"格式={status.get('format', '')} 原因={status.get('error', '未知错误')}"
                )
            )

    def apply_runtime_signal_overrides(
        self,
        service: SignalOverrideService,
        scenario: ScenarioSpec,
    ) -> None:
        service.clear_overrides()
        for override in scenario.signal_overrides:
            service.set_override(override)
        for override in self._workspace_overrides():
            service.set_override(override)


__all__ = ("RuntimeOverrideCoordinator",)
