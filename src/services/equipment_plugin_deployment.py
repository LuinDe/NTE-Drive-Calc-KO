# 提供打包装备插件的显式、可恢复部署能力。
"""Explicit, reversible deployment helpers for the packaged game plugin."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


GAME_EXECUTABLE_NAME = "HTGame.exe"
PLUGIN_FILENAME = "dwmapi.dll"
MOD_PLUGIN_SIGNATURE = b"NTE_DPS_TOOL_MODS_PLUGIN_V1"
PACKAGED_PLUGIN_RELATIVE_PATH = Path("third_party") / "mods-plugin" / "bin" / PLUGIN_FILENAME
LEGACY_PACKAGED_PLUGIN_RELATIVE_PATH = (
    Path("third_party") / "equipment-plugin" / "bin" / PLUGIN_FILENAME
)
PACKAGED_MOD_WORKSPACE_RELATIVE_PATH = Path("third_party") / "mods-plugin" / "workspace"
MOD_WORKSPACE_REGISTRY_KEY = r"Software\NTE DPS Tool\Mods Plugin"
MOD_WORKSPACE_REGISTRY_VALUE = "Workspace"
MOD_WORKSPACE_FILES = (
    Path("nte-mods.enabled"),
    Path("mods-plugin.version"),
    Path("README.md"),
    Path("nte-mods") / "equipment.nte",
    Path("nte-mods") / "combat-clock.nte",
)
# The game-side DLL owns these files.  They are generated from the running
# HTGame image and must never be bundled or overwritten during a workspace
# refresh.
MOD_SDK_CACHE_FILES = (
    Path("NTE_SDK.bin"),
    Path("NTE_SDK.checksum"),
)
_MANAGED_WORKSPACE_MANIFEST = ".nte-drive-calc-managed.json"
STANDARD_GAME_EXECUTABLE_RELATIVE_PATH = (
    Path("Neverness To Everness")
    / "Client"
    / "WindowsNoEditor"
    / "HT"
    / "Binaries"
    / "Win64"
    / GAME_EXECUTABLE_NAME
)
GAME_EXECUTABLE_WITHIN_INSTALL_PATH = Path(
    *STANDARD_GAME_EXECUTABLE_RELATIVE_PATH.parts[1:]
)
COMMON_GAME_LIBRARY_DIRECTORIES = (
    "games",
    "Games",
    "game",
    "Game",
    "Program Files",
    "Program Files (x86)",
)
_WINDOWS_UNINSTALL_REGISTRY_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
)


class EquipmentPluginDeploymentError(RuntimeError):
    """The selected game or plugin file cannot be deployed safely."""


@dataclass(frozen=True)
class PluginDeployment:
    game_executable: Path
    target_path: Path
    backup_path: Path | None
    deployed_sha256: str
    workspace_path: Path
    workspace_registry_value_before: str | None = None
    workspace_registry_value_existed: bool = False


def game_process_running() -> bool:
    """Return whether the game executable is currently present in Windows tasks."""

    try:
        result = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {GAME_EXECUTABLE_NAME}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    expected = GAME_EXECUTABLE_NAME.casefold()
    return any(
        line.split(",", 1)[0].strip().strip('"').casefold() == expected
        for line in result.stdout.splitlines()
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def game_executable(path: str | Path) -> Path:
    # Explorer's “复制文件地址” commonly yields a quoted absolute path.
    raw_path = str(path).strip().strip('"')
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_file() or candidate.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError(
            f"폴더나 다른 실행 파일이 아닌 게임 실행 파일 {GAME_EXECUTABLE_NAME}을(를) 선택하세요"
        )
    return candidate


def plugin_dll(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file() or candidate.name.casefold() != PLUGIN_FILENAME:
        raise EquipmentPluginDeploymentError(
            f"제공자가 승인한 {PLUGIN_FILENAME} 파일을 선택하세요"
        )
    return candidate


def is_mods_plugin_dll(path: str | Path) -> bool:
    """Return whether the DLL carries the public nte-mods-plugin marker."""

    candidate = plugin_dll(path)
    overlap = len(MOD_PLUGIN_SIGNATURE) - 1
    tail = b""
    try:
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                if MOD_PLUGIN_SIGNATURE in tail + chunk:
                    return True
                tail = (tail + chunk)[-overlap:]
    except OSError:
        return False
    return False


def packaged_plugin_dll(application_root: str | Path) -> Path:
    """Return the packaged plugin, preferring the source-tree component layout.

    PyInstaller releases keep the DLL beside the executable for compatibility with
    existing installs, while source builds keep it under ``third_party``.
    """

    root = Path(application_root)
    candidates = (
        root / PACKAGED_PLUGIN_RELATIVE_PATH,
        root / PLUGIN_FILENAME,
        root / LEGACY_PACKAGED_PLUGIN_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            plugin = plugin_dll(candidate)
            if is_mods_plugin_dll(plugin):
                return plugin
    checked = "、".join(str(candidate) for candidate in candidates)
    raise EquipmentPluginDeploymentError(
        f"새 버전 MOD 서명이 있는 {PLUGIN_FILENAME}을(를) 찾지 못했습니다. 확인한 위치: {checked}"
    )


def packaged_mod_workspace(application_root: str | Path) -> Path:
    """Return the release-matched NTE Script workspace bundled with the plugin."""

    root = Path(application_root)
    candidates = (
        root / PACKAGED_MOD_WORKSPACE_RELATIVE_PATH,
        root / "plugins",
    )
    for candidate in candidates:
        if all((candidate / relative).is_file() for relative in MOD_WORKSPACE_FILES):
            return candidate.resolve()
    checked = "、".join(str(candidate) for candidate in candidates)
    raise EquipmentPluginDeploymentError(
        f"{PLUGIN_FILENAME}과(와) 짝을 이루는 nte-mods 작업 공간을 찾지 못했습니다. 확인한 위치: {checked}"
    )


def _managed_workspace_hashes(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EquipmentPluginDeploymentError(f"관리되는 Mod 작업 공간 기록을 읽을 수 없습니다: {path}") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(files, dict)
        or any(
            not isinstance(name, str) or not isinstance(digest, str)
            for name, digest in files.items()
        )
    ):
        raise EquipmentPluginDeploymentError(f"관리되는 Mod 작업 공간 기록 형식이 잘못되었습니다: {path}")
    return files


def mod_workspace_registry_snapshot() -> tuple[bool, str | None]:
    """Return whether the global Mods workspace value exists and its value."""

    if os.name != "nt":
        return False, None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_QUERY_VALUE,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, MOD_WORKSPACE_REGISTRY_VALUE)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        raise EquipmentPluginDeploymentError("nte-mods 작업 공간 레지스트리를 읽을 수 없습니다") from exc
    if value_type != winreg.REG_SZ or not isinstance(value, str):
        return False, None
    return True, value


def _register_mod_workspace(workspace: Path) -> tuple[bool, str | None]:
    if os.name != "nt":
        raise EquipmentPluginDeploymentError("nte-mods 작업 공간 등록은 Windows만 지원합니다")
    import winreg

    previous_exists, previous_value = mod_workspace_registry_snapshot()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                MOD_WORKSPACE_REGISTRY_VALUE,
                0,
                winreg.REG_SZ,
                str(workspace),
            )
    except OSError as exc:
        raise EquipmentPluginDeploymentError("nte-mods 작업 공간을 등록할 수 없습니다") from exc
    return previous_exists, previous_value


def registered_mod_workspace() -> Path | None:
    """Return the workspace currently visible to nte-mods-plugin."""

    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_QUERY_VALUE,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, MOD_WORKSPACE_REGISTRY_VALUE)
    except OSError:
        return None
    if value_type != winreg.REG_SZ or not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def restore_mod_workspace(
    *,
    workspace_path: str | Path | None,
    previous_value: str | None,
    previous_value_existed: bool,
) -> bool:
    """Restore the previous registry value only while this app still owns it."""

    if workspace_path is None or os.name != "nt":
        return False
    workspace = Path(workspace_path).expanduser().resolve()
    current_exists, current_value = mod_workspace_registry_snapshot()
    if not current_exists or current_value != str(workspace):
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            if previous_value_existed:
                winreg.SetValueEx(
                    key,
                    MOD_WORKSPACE_REGISTRY_VALUE,
                    0,
                    winreg.REG_SZ,
                    str(previous_value or ""),
                )
            else:
                try:
                    winreg.DeleteValue(key, MOD_WORKSPACE_REGISTRY_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise EquipmentPluginDeploymentError("nte-mods 작업 공간 레지스트리를 복원할 수 없습니다") from exc
    return True


def prepare_mod_workspace(
    *,
    application_root: str | Path,
    writable_workspace_path: str | Path,
    register_workspace: bool = True,
) -> Path:
    """Install release defaults without replacing user scripts or SDK cache.

    The current plugin generates ``NTE_SDK.bin`` for the loaded game image and
    validates it with ``NTE_SDK.checksum`` on later launches.  Those runtime
    artifacts intentionally remain outside ``MOD_WORKSPACE_FILES``.
    """

    source = packaged_mod_workspace(application_root)
    destination = Path(writable_workspace_path).expanduser().resolve()
    # The bundled third_party workspace is now the canonical live workspace.
    # Do not copy a file onto itself or create a managed manifest beside the
    # release scripts: the game owns its SDK cache in that same directory.
    if source == destination:
        if register_workspace:
            _register_mod_workspace(destination)
        return destination
    try:
        destination.mkdir(parents=True, exist_ok=True)
        manifest_path = destination / _MANAGED_WORKSPACE_MANIFEST
        previous_hashes = _managed_workspace_hashes(manifest_path)
        managed_hashes: dict[str, str] = {}

        for relative in MOD_WORKSPACE_FILES:
            source_file = source / relative
            destination_file = destination / relative
            source_hash = _file_sha256(source_file)
            relative_name = relative.as_posix()
            if destination_file.is_file():
                current_hash = _file_sha256(destination_file)
                previous_hash = previous_hashes.get(relative_name)
                if current_hash != source_hash and current_hash != previous_hash:
                    continue
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
            managed_hashes[relative_name] = source_hash

        manifest_path.write_text(
            json.dumps(
                {"version": 1, "files": managed_hashes},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except EquipmentPluginDeploymentError:
        raise
    except OSError as exc:
        raise EquipmentPluginDeploymentError(
            f"nte-mods 작업 공간을 준비할 수 없습니다: {destination}"
        ) from exc

    if register_workspace:
        _register_mod_workspace(destination)
    return destination


def _disk_roots() -> list[Path]:
    """Return Windows volume roots without walking any directory tree."""
    roots: list[Path] = []
    if os.name == "nt":
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for offset in range(26):
            if mask & (1 << offset):
                roots.append(Path(f"{chr(ord('A') + offset)}:\\"))
    return roots


def _registry_executable_path(value: str) -> Path | None:
    """Extract the executable from an uninstall command or display-icon value."""

    raw_value = value.strip()
    if not raw_value:
        return None
    if raw_value.startswith('"'):
        closing_quote = raw_value.find('"', 1)
        if closing_quote > 1:
            return Path(raw_value[1:closing_quote])
    executable_end = raw_value.casefold().find(".exe")
    if executable_end >= 0:
        return Path(raw_value[: executable_end + len(".exe")].strip().strip('"'))
    return None


def _is_nte_registry_entry(
    key_name: str,
    *,
    display_name: str,
    display_icon: str,
) -> bool:
    normalized_name = display_name.casefold()
    icon_path = _registry_executable_path(display_icon)
    icon_name = icon_path.name.casefold() if icon_path is not None else ""
    return (
        key_name.casefold() == "yh"
        or "异环" in display_name
        or "neverness to everness" in normalized_name
        or icon_name in {"ntelauncher.exe", "ntegloballauncher.exe"}
    )


def _registry_game_roots() -> list[Path]:
    """Read likely NTE install roots from Windows uninstall registration."""

    if os.name != "nt":
        return []
    import winreg

    registry_roots: list[Path] = []
    access_views = {
        getattr(winreg, "KEY_WOW64_64KEY", 0),
        getattr(winreg, "KEY_WOW64_32KEY", 0),
    }
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access_view in access_views:
            try:
                uninstall_key = winreg.OpenKey(
                    hive,
                    _WINDOWS_UNINSTALL_REGISTRY_PATH,
                    0,
                    winreg.KEY_READ | access_view,
                )
            except OSError:
                continue
            with uninstall_key:
                subkey_index = 0
                while True:
                    try:
                        key_name = winreg.EnumKey(uninstall_key, subkey_index)
                    except OSError:
                        break
                    subkey_index += 1
                    try:
                        product_key = winreg.OpenKey(
                            uninstall_key,
                            key_name,
                            0,
                            winreg.KEY_READ,
                        )
                    except OSError:
                        continue
                    with product_key:
                        values: dict[str, str] = {}
                        for value_name in (
                            "DisplayName",
                            "DisplayIcon",
                            "InstallLocation",
                            "UninstallString",
                        ):
                            try:
                                raw_value = winreg.QueryValueEx(
                                    product_key,
                                    value_name,
                                )[0]
                            except OSError:
                                raw_value = ""
                            values[value_name] = (
                                raw_value if isinstance(raw_value, str) else ""
                            )
                    if not _is_nte_registry_entry(
                        key_name,
                        display_name=values["DisplayName"],
                        display_icon=values["DisplayIcon"],
                    ):
                        continue
                    install_location = values["InstallLocation"].strip().strip('"')
                    if install_location:
                        registry_roots.append(Path(install_location))
                    for value_name in ("DisplayIcon", "UninstallString"):
                        registered_executable = _registry_executable_path(
                            values[value_name]
                        )
                        if registered_executable is None:
                            continue
                        launcher_root = registered_executable.parent
                        if launcher_root.name.casefold() == "ntelauncher":
                            launcher_root = launcher_root.parent
                        registry_roots.append(launcher_root)
    return registry_roots


def _candidate_game_executables(root: Path) -> tuple[Path, ...]:
    if root.name.casefold() == GAME_EXECUTABLE_NAME.casefold():
        return (root,)
    candidates = [root / STANDARD_GAME_EXECUTABLE_RELATIVE_PATH]
    if root.name.casefold() == "neverness to everness":
        candidates.insert(0, root / GAME_EXECUTABLE_WITHIN_INSTALL_PATH)
    return tuple(candidates)


def _default_game_search_roots() -> list[Path]:
    roots = list(_registry_game_roots())
    for volume_root in _disk_roots():
        roots.append(volume_root)
        roots.extend(
            volume_root / directory
            for directory in COMMON_GAME_LIBRARY_DIRECTORIES
        )
    return roots


def find_game_executables(
    search_roots: list[str | Path] | None = None,
    *,
    limit: int = 20,
) -> list[Path]:
    """Find NTE via registered install roots and bounded standard layouts."""

    if limit <= 0:
        return []
    candidates: dict[str, Path] = {}
    roots = search_roots if search_roots is not None else _default_game_search_roots()
    for raw_root in roots:
        root = Path(raw_root).expanduser()
        for possible_executable in _candidate_game_executables(root):
            if not possible_executable.is_file():
                continue
            resolved = possible_executable.resolve()
            candidates.setdefault(str(resolved).casefold(), resolved)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    return list(candidates.values())


def deploy_plugin(
    *,
    game_executable_path: str | Path,
    plugin_dll_path: str | Path,
    application_root: str | Path,
    writable_workspace_path: str | Path,
    backup_directory: str | Path,
) -> PluginDeployment:
    """Prepare the script workspace, deploy the plugin, and preserve any DLL."""
    executable = game_executable(game_executable_path)
    source = plugin_dll(plugin_dll_path)
    if not is_mods_plugin_dll(source):
        raise EquipmentPluginDeploymentError(
            f"선택한 {PLUGIN_FILENAME}은(는) 현재 스크립트와 맞는 새 버전 nte-mods-plugin이 아닙니다"
        )
    workspace = prepare_mod_workspace(
        application_root=application_root,
        writable_workspace_path=writable_workspace_path,
        register_workspace=False,
    )
    target = executable.parent / PLUGIN_FILENAME
    if source == target:
        raise EquipmentPluginDeploymentError("선택한 플러그인이 이미 대상 게임 디렉터리에 있어 다시 배포할 필요가 없습니다")

    source_hash = _file_sha256(source)
    target_existed_before = target.exists()
    backup_path: Path | None = None
    if target.exists() and _file_sha256(target) != source_hash:
        backup_root = Path(backup_directory).expanduser().resolve()
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / f"{target.parent.name}_{PLUGIN_FILENAME}.{_file_sha256(target)[:16]}.bak"
        if not backup_path.exists():
            shutil.copy2(target, backup_path)
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise EquipmentPluginDeploymentError(
            f"게임 디렉터리에 기록할 수 없습니다: {target}. 게임을 닫고 해당 디렉터리에 쓰기 권한이 있는 계정으로 다시 시도하세요."
        ) from exc
    try:
        registry_existed, registry_value = _register_mod_workspace(workspace)
    except EquipmentPluginDeploymentError as exc:
        try:
            if backup_path is not None and backup_path.is_file():
                shutil.copy2(backup_path, target)
            elif not target_existed_before and target.is_file():
                target.unlink()
        except OSError as rollback_exc:
            raise EquipmentPluginDeploymentError(
                "MOD 작업 공간 등록에 실패했고 게임 디렉터리 DLL 자동 롤백도 실패했습니다."
                f"게임을 닫은 채 {target}을(를) 직접 확인하세요"
            ) from rollback_exc
        raise EquipmentPluginDeploymentError(
            "MOD 작업 공간 등록에 실패해 게임 디렉터리 DLL을 롤백했습니다:"
            + str(exc)
        ) from exc
    return PluginDeployment(
        executable,
        target,
        backup_path,
        source_hash,
        workspace,
        registry_value,
        registry_existed,
    )


def restore_plugin(
    *,
    game_executable_path: str | Path,
    deployed_sha256: str,
    backup_path: str | Path | None,
    mod_workspace_path: str | Path | None = None,
    workspace_registry_value_before: str | None = None,
    workspace_registry_value_existed: bool = False,
) -> bool:
    """Restore the backed-up DLL and the previous global Mod workspace."""
    executable = game_executable(game_executable_path)
    target = executable.parent / PLUGIN_FILENAME
    if not target.is_file():
        raise EquipmentPluginDeploymentError("게임 디렉터리에 복원할 dwmapi.dll이 없습니다")
    if _file_sha256(target) != str(deployed_sha256).strip().lower():
        raise EquipmentPluginDeploymentError(
            "대상 dwmapi.dll이 다른 프로그램에 의해 수정되었습니다. 다른 파일을 덮어쓰지 않도록 복원을 거부했습니다"
        )
    try:
        restored_backup = False
        if backup_path:
            backup = Path(backup_path).expanduser().resolve()
            if backup.is_file():
                shutil.copy2(backup, target)
                restored_backup = True
        if not restored_backup:
            target.unlink()
    except OSError as exc:
        raise EquipmentPluginDeploymentError("게임 디렉터리의 dwmapi.dll을 복원할 수 없습니다. 게임이 닫혀 있는지 확인하세요") from exc
    return restore_mod_workspace(
        workspace_path=mod_workspace_path,
        previous_value=workspace_registry_value_before,
        previous_value_existed=workspace_registry_value_existed,
    )


def npcap_installation_present() -> bool:
    """Best-effort local Npcap detection without launching a subprocess."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return (Path(program_files) / "Npcap" / "NPFInstall.exe").is_file()
