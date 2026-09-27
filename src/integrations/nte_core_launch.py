# 根据明确采集来源构建 Core 命令，不将原生缺失回退为抓包。
from pathlib import Path
from src.integrations.nte_core_protocol import NteCoreProcessError


def core_serve_command(base: list[str], *, game_pid: int | None,
                       required_source: str | None, data_dir: Path | None,
                       log_level: str | None) -> list[str]:
    if required_source == "native" and game_pid is None:
        raise NteCoreProcessError("게임 내 네이티브 컴포넌트 파이프 대기 중; 현재 모드는 패킷 캡처로 전환되지 않습니다.")
    command = ([*base, "serve-native", "--stdio", "--game-pid", str(game_pid)]
               if game_pid is not None else [*base, "serve", "--stdio"])
    if data_dir is not None:
        command.extend(["--data-dir", str(data_dir)])
    if log_level and game_pid is None:
        command.extend(["--log-level", log_level])
    return command
