# 将开启同步前的环境事实投影为可操作的下一步，不执行组件写入。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.work_mode import CheckState, WorkMode, WorkModeProbe, WorkModeSettings


@dataclass(frozen=True)
class SyncEnableDecision:
    ready: bool
    detail: str
    target: str = "deployment"
    action_label: str | None = None


def decide_sync_enable(
    settings: WorkModeSettings, record: dict, probe: WorkModeProbe,
) -> SyncEnableDecision:
    if settings.mode == WorkMode.OFFLINE:
        return SyncEnableDecision(False, "현재 오프라인 모드입니다; 먼저 사용 가능한 작업 모드를 확인해 주세요.",
                                  "mode", "작업 모드 설정으로 이동")
    if not settings.risk_confirmed:
        return SyncEnableDecision(False, "현재 작업 모드가 아직 확인되지 않았습니다; 먼저 모드 확인을 완료해 주세요.",
                                  "mode", "작업 모드 설정으로 이동")
    if settings.mode == WorkMode.LOW:
        if probe.core_available is not True:
            return SyncEnableDecision(False, "패킷 캡처 Core가 아직 확인을 통과하지 못했습니다. 설치된 컴포넌트를 점검하세요.",
                                      "core", "환경 설정으로 이동" if probe.core_available is False else None)
        if probe.npcap_available is not True:
            return SyncEnableDecision(False, "Npcap 사용 가능 여부가 아직 확인되지 않았습니다; 환경 설정에서 설치하거나 다시 검사하세요.", "npcap")
        return SyncEnableDecision(True, "패킷 캡처 조건 확인 완료; 수신 대기를 켠 후 게임과 로그인 데이터를 기다리세요.")
    if probe.game_path_valid is not True:
        return SyncEnableDecision(False, "게임 경로가 아직 확인되지 않았습니다; 환경 설정에서 HTGame.exe를 선택하세요.",
                                  "game_path", "게임 경로 확인으로 이동" if probe.game_path_valid is False else None)
    if probe.core_available is not True or probe.component_update_state == CheckState.MISSING:
        return SyncEnableDecision(False, "네이티브 컴포넌트 패키지가 아직 대조를 통과하지 못했습니다. 설치된 컴포넌트를 확인하세요.",
                                  "deployment", "환경 설정으로 이동" if probe.core_available is False or
                                  probe.component_update_state == CheckState.MISSING else None)
    if probe.component_update_state == CheckState.FAULT:
        return SyncEnableDecision(False, "컴포넌트 점검이 중단되었습니다; 검사 상세 정보에서 원인을 확인하세요.",
                                  "deployment", "환경 설정으로 이동")
    if settings.pending_cleanup:
        detail = ("먼저 게임을 완전히 종료한 후 이전 컴포넌트를 정리하세요." if probe.game_running else
                  "구버전 컴포넌트가 아직 정리되지 않았습니다; 먼저 게임 디렉터리를 정리하세요.")
        return SyncEnableDecision(False, detail, "deployment", "게임 디렉터리 정리로 이동")
    if probe.native_load.files is False:
        if probe.game_running:
            detail = ("먼저 런처를 닫고 게임을 완전히 종료한 후 Loader를 배포하세요." if
                      record.get("loading_method") == "loader" else
                      "먼저 게임을 완전히 종료한 후 현재 컴포넌트를 배포하세요.")
            return SyncEnableDecision(False, detail, "deployment", "컴포넌트 배포로 이동")
        if record.get("loading_method") == "loader":
            if probe.launcher_probe_error:
                return SyncEnableDecision(False, "런처 상태 확인이 중단되었습니다; 런처와 게임을 종료한 후 다시 검사해 주세요.",
                                          "deployment", "컴포넌트 배포로 이동")
            if probe.launcher_running is None:
                return SyncEnableDecision(False, "런처 프로세스 상태가 아직 확인되지 않았습니다. 다시 검사해 주세요.")
            if probe.launcher_running:
                return SyncEnableDecision(False, "먼저 런처와 게임을 종료한 후 Loader를 배포하세요.",
                                          "deployment", "컴포넌트 배포로 이동")
        detail = "현재 컴포넌트가 아직 배포되지 않았습니다; 먼저 배포한 후 돌아와 동기화를 켜 주세요."
        return SyncEnableDecision(False, detail,
                                  "deployment", "컴포넌트 배포로 이동")
    if probe.native_load.files is not True:
        return SyncEnableDecision(False, "컴포넌트 파일 상태가 아직 확인되지 않았습니다. 다시 검사하세요.")
    return SyncEnableDecision(
        True,
        ("현재 컴포넌트 파일은 대조되었습니다; 동기화를 켜면 이전 정리로 남은 일시 중지가 해제되며, 실제 데이터는 게임이 준비될 때까지 기다려야 합니다."
         if settings.paused else "현재 컴포넌트 파일은 대조되었습니다; 실제 데이터는 게임이 준비될 때까지 기다려야 합니다."),
    )


def decide_sync_activation(settings: WorkModeSettings, probe: WorkModeProbe) -> SyncEnableDecision:
    """Confirm the post-action result before persisting synchronization on."""
    if settings.mode == WorkMode.LOW:
        if probe.core_available is True and probe.npcap_available is True:
            return SyncEnableDecision(True, "패킷 캡처 조건이 준비되었습니다; 켠 후 게임과 로그인 데이터를 기다리세요.")
        return SyncEnableDecision(False, "패킷 캡처 조건이 아직 준비되지 않았습니다. 다시 검사하세요.", "npcap")
    if settings.pending_cleanup:
        return SyncEnableDecision(False, "컴포넌트 정리가 아직 완료되지 않았습니다; 검사 상세 정보를 확인하세요.")
    if probe.component_update_state == CheckState.FAULT:
        return SyncEnableDecision(False, "컴포넌트 점검이 중단되었습니다; 검사 상세 정보를 확인하세요.")
    if probe.native_load.files is not True:
        return SyncEnableDecision(False, "컴포넌트 배포가 아직 완료되지 않았습니다; 다시 검사하세요.")
    return SyncEnableDecision(True, "컴포넌트 파일 점검이 끝났습니다; 활성화한 후에도 게임 연결과 완전한 비즈니스 데이터를 기다려야 합니다.")
