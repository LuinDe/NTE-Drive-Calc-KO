# 只读核对旧组件证据并生成升级引导状态，不修改游戏或账号数据。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.integrations.legacy_game_proxy import legacy_game_proxy_present
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError, mod_workspace_registry_snapshot,
)


@dataclass(frozen=True)
class UpgradeEvidence:
    kind: str
    reason: str
    detail: str
    method: str

    @property
    def requires_attention(self) -> bool:
        return self.kind in {"legacy", "update", "path_unknown"}


def inspect_upgrade_evidence(*, application_root: Path, game_path: str,
                             deployment: Mapping, loader=None) -> UpgradeEvidence:
    """Inspect only the configured/recorded game and registered workspace."""
    method = "loader" if deployment.get("loading_method") == "loader" else "native-capture"
    recorded_path = str(deployment.get("game_executable") or "")
    raw_path = (recorded_path or game_path).strip().strip('"')
    path = Path(raw_path).expanduser() if raw_path else None
    has_record = any(deployment.get(key) for key in (
        "deployed_sha256", "managed_files", "workspace_path", "native_workspace_root",
    ))
    if path is None or not path.is_absolute() or path.name.casefold() != "htgame.exe":
        if has_record:
            return UpgradeEvidence("path_unknown", "이전 배포 기록의 게임 위치가 아직 확인되지 않았습니다.",
                                   "환경 설정에서 게임 메인 실행 파일을 다시 선택하세요; 검색으로 찾은 다른 디렉터리를 대신 정리하지는 않습니다.", method)
        return UpgradeEvidence("none", "아직 이전 플러그인 배포 증거가 없습니다.", "", method)
    game_directory = path.parent
    try:
        proxy_present = legacy_game_proxy_present(game_directory)
    except OSError as error:
        return UpgradeEvidence("path_unknown", "이전 컴포넌트 상태 읽기에 실패했습니다.", type(error).__name__, method)
    old_workspace = str(deployment.get("workspace_path") or "")
    old_record = has_record and deployment.get("deployment_layout") != "native-capture-v1"
    if proxy_present or old_record:
        try:
            registered, registry_path = mod_workspace_registry_snapshot()
        except (OSError, EquipmentPluginDeploymentError) as error:
            return UpgradeEvidence("path_unknown", "이전 작업 영역 등록 정보 읽기에 실패했습니다.", type(error).__name__, method)
        reasons = []
        if proxy_present:
            reasons.append("게임 폴더에 구버전 로드 파일 dwmapi.dll이 있습니다")
        if old_record:
            reasons.append("구버전 배포 기록이 남아 있음")
        if old_workspace and registered and registry_path:
            try:
                if Path(old_workspace).expanduser().resolve() != Path(registry_path).expanduser().resolve():
                    reasons.append("Mod 작업 공간 등록이 이전 기록과 다름")
            except OSError:
                reasons.append("Mod 작업 공간 등록 재대조 필요")
        return UpgradeEvidence("legacy", "；".join(reasons) + ".",
                               "구버전 컴포넌트를 먼저 정리한 다음 현재 로드 방식에 따라 배포해야 합니다.", method)
    if not path.is_file():
        if has_record:
            return UpgradeEvidence("path_unknown", "원래 게임 메인 실행 파일이 기록된 위치에 더 이상 없습니다.",
                                   "먼저 환경 설정에서 게임 위치를 확인하세요; 이전 기록은 기존 디렉터리대로 처리됩니다.", method)
        return UpgradeEvidence("none", "아직 이전 플러그인 배포 증거가 없습니다.", "", method)
    if method == "loader":
        if not deployment.get("native_workspace_root"):
            return UpgradeEvidence("none", "아직 Loader 배포 증거가 없습니다.", "", method)
        try:
            compatible = loader.inspect_native_workspace().files_compatible
        except (OSError, ValueError, AttributeError, EquipmentPluginDeploymentError) as error:
            return UpgradeEvidence("path_unknown", "Loader 작업 공간 대조에 실패했습니다.", type(error).__name__, method)
    else:
        if not (has_record or (game_directory / "d3d12.dll").is_file()
                or (game_directory / "NTE_Capture.dll").is_file()):
            return UpgradeEvidence("none", "아직 이전 플러그인 배포 증거가 없습니다.", "", method)
        inspection = inspect_deployed_native_plugin(
            application_root=application_root, game_executable_path=path,
            recorded_files=deployment.get("managed_files") or {},
        )
        compatible = inspection.files_compatible
    if not compatible:
        return UpgradeEvidence("update", "현재 컴포넌트가 이 버전에 동봉된 파일과 일치하지 않습니다.",
                               "먼저 게임과 런처를 종료하고 이전 구성 요소를 정리한 뒤, 현재 버전을 배포하세요.", method)
    return UpgradeEvidence("ready", "컴포넌트 파일이 현재 버전과 일치합니다.",
                           "파일 확인이 곧 게임 내 연결과 동기화 준비를 뜻하지는 않습니다.", method)
