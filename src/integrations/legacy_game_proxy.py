# 仅供明确手动清理的调用方核对并移除游戏目录旧代理入口。
from __future__ import annotations

from pathlib import Path
import stat
from typing import Callable


LEGACY_GAME_PROXY = 'dwmapi.dll'


def legacy_game_proxy_present(game_directory: Path) -> bool:
    """Include broken links and directories so management cannot skip a conflict."""
    target = game_directory / LEGACY_GAME_PROXY
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    return True


def _identity(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError('게임 폴더의 dwmapi.dll이 안전하게 관리할 수 있는 일반 파일이 아니어서 네이티브 컴포넌트 관리를 중단했습니다.')
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def remove_legacy_game_proxy(
    *, game_directory: Path, require_idle: Callable[[], None],
) -> None:
    """Delete only the old game-local deployment copy, without keeping a backup."""
    require_idle()
    directory = Path(game_directory).resolve()
    target = directory / LEGACY_GAME_PROXY
    if not legacy_game_proxy_present(directory):
        return
    original = _identity(target)
    require_idle()
    if _identity(target) != original:
        raise OSError('이전 프록시가 제거 전에 변경되어 게임 디렉터리의 dwmapi.dll을 삭제하지 않았습니다.')
    require_idle()
    if _identity(target) != original:
        raise OSError('이전 프록시가 제거 전에 변경되어 게임 디렉터리의 dwmapi.dll을 삭제하지 않았습니다.')
    target.unlink()
