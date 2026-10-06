# 仅供明确手动清理的调用方核对并移除游戏目录旧代理入口。
from __future__ import annotations

from pathlib import Path
import stat
from typing import Callable


LEGACY_GAME_PROXY = 'dwmapi.dll'


# KO patch (fix_protect_foreign_231): the calculator's own old dwmapi.dll proxies.  Any other dwmapi.dll
# (UE4SS, other mods) is not ours: it is never deleted and does not count as a leftover proxy.
_KO_OFFICIAL_PROXY_SHA256 = frozenset({
    '05b9cbed26ba6fd7837ca3ea21d78caa249b953cb76a22e954a486a3406d4b6f', '27f8c418d961a5cc5645fba5e98e84df0cb92ef3ff83fde264220c510fc58d34',
    '46da088eb62eb0a02c5488356f5b47e63a25e3d887d6b365db98f3a7828ea5d5', '4caa672401237d511cff756f2694c7785b20b4c615fefbd9ef09282e90c10f07',
    '6d5a4b2c48bbee97c6463bca36aa2e3b2aa00849991ea6f0ddf7a0f28c76f133', '9b59c1dd756202506dc0f0f6306aab6163980f2c01f5e8588eab97f7b132ffda',
    'ac1f62fdccde7d4611bc3e027a8ceae9312109d4bce32575e7d5cf62602a301a', 'b47928d594be93b1e928df79c9357a54a2a526a8eae4f1dedb3d544ca1612f03',
    'bfc54db88a0f9738aa31a4447eb5add6efefffbf28e466c75a5d1b84211cb59e', 'ffd549ef970b0d0bb16ff952a7ad8fb7d4b914245f9dad5f1e083ebb15ce213d',
})
KO_KEPT_FOREIGN: list[str] = []
KO_RECORDED_PROXY_SHA256: set[str] = set()


def ko_note_recorded_proxy(record) -> None:
    """A 2.0-2.2.x record names the dwmapi.dll this app deployed itself (deployed_sha256)."""
    try:
        digest = str(record.get('deployed_sha256') or '').strip().casefold()
        if len(digest) == 64 and not str(record.get('deployment_layout') or '').startswith('native-'):
            KO_RECORDED_PROXY_SHA256.add(digest)
    except AttributeError:
        pass


def ko_is_foreign_proxy(path) -> bool:
    """True for a regular file whose SHA256 is not one of the calculator's old proxies."""
    import hashlib
    try:
        info = Path(path).lstat()
        if (not stat.S_ISREG(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            return False   # links and directories stay an upstream conflict
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() not in (_KO_OFFICIAL_PROXY_SHA256 | KO_RECORDED_PROXY_SHA256)
    except FileNotFoundError:
        return False
    except OSError:
        return True    # unreadable: cannot prove it is the calculator's proxy - keep it


def ko_kept_note() -> str:
    """One-shot note for a success dialog after a foreign dwmapi.dll was left alone."""
    names = list(dict.fromkeys(KO_KEPT_FOREIGN))
    KO_KEPT_FOREIGN.clear()
    return ('\n\n' + '게임 폴더의 {0}은(는) 이 프로그램의 옛 로더 파일이 아니어서(UE4SS 등 다른 프로그램의 파일일 수 있음) 삭제하지 않고 그대로 두었습니다.'.format(', '.join(names))) if names else ''


def legacy_game_proxy_present(game_directory: Path) -> bool:
    """Include broken links and directories so management cannot skip a conflict."""
    target = game_directory / LEGACY_GAME_PROXY
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    return not ko_is_foreign_proxy(target)   # KO patch: a foreign dwmapi.dll is no leftover proxy


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
    if ko_is_foreign_proxy(target):   # KO patch: not the calculator's old proxy (e.g. UE4SS) - keep it
        KO_KEPT_FOREIGN.append(LEGACY_GAME_PROXY)
        return
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
