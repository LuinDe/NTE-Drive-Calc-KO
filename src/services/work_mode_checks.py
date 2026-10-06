# 将独立运行事实投影为逐功能检测结果，不执行安装或加载。
from src.domain.work_mode import (
    Capability, CheckState, FeatureCheck, NativeFeatureProbe, WorkModeProbe,
    WorkMode, WorkModeReport, WorkModeSettings, allowed_capabilities,
)
from src.integrations.nte_core_protocol import (
    NATIVE_CAPTURE_TRANSIENT_REASONS, NteCoreRpcError, native_capture_readiness_message,
)
from src.services.sync_enable_preflight import decide_sync_enable


_LOGIN_WAIT_DETAIL = "로그인하여 게임 장면에 진입하세요; 이미 진입했다면 전체 데이터 동기화를 기다리세요. 기존 데이터는 그대로 유지됩니다."
_NATIVE_DATA_FAULTS = frozenset({
    "NATIVE_MAPPING_UNSUPPORTED", "NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_CAPABILITY_MISSING",
    "NATIVE_SNAPSHOT_UNAVAILABLE", "PROTOCOL_VERSION_MISMATCH", "HANDSHAKE_REQUIRED",
})


def _native_check(
    feature: str, label: str, native: NativeFeatureProbe,
    probe: WorkModeProbe, *, needs_snapshot: bool, external_provider_allowed: bool = False,
) -> FeatureCheck:
    facts = tuple((name, getattr(native, name)) for name in (
        "files", "pipe", "handshake", "supported", "snapshot", "ready",
        "complete", "source_coverage", "projection_complete", "profile_projection_supported",
    ))

    def result(state: CheckState, detail: str, *actions: str) -> FeatureCheck:
        return FeatureCheck(feature, label, state, detail, actions, facts)

    if native.fault:
        return result(CheckState.FAULT, native.fault, "recheck")
    if probe.game_path_valid is None:
        return result(CheckState.WAITING, "게임 디렉터리를 확인하는 중입니다.", "recheck")
    if probe.game_path_valid is False:
        return result(CheckState.MISSING, "게임 디렉터리가 아직 확인되지 않았습니다.", "detect_game_path")
    external_connection = (external_provider_allowed and feature != "native_load"
                           and native.pipe is True and native.handshake is True)
    if native.reason == "packaged_capability_missing":
        return result(CheckState.MISSING, "현재 함께 제공되는 네이티브 플러그인이 이 기능에 필요한 완전한 능력을 제공하지 않습니다; 이미 지원되는 다른 기능은 단독으로 사용할 수 있습니다.", "recheck")
    if native.files is None and not external_connection:
        return result(CheckState.WAITING, "게임 내 컴포넌트 파일 대조가 아직 완료되지 않았습니다.", "recheck")
    if native.files is False and not external_connection:
        if probe.core_available is not True:
            return result(
                CheckState.MISSING if probe.core_available is False else CheckState.WAITING,
                "매칭 컴포넌트 패키지가 아직 점검을 통과하지 못해 지금은 배포할 수 없습니다." if probe.core_available is False else
                "함께 제공되는 컴포넌트 패키지를 확인하는 중입니다.", "recheck",
            )
        return result(CheckState.MISSING, "게임 내 컴포넌트가 배포되지 않았거나 현재 컴포넌트 패키지와 호환되지 않습니다; 컴포넌트 배포로 이동해 배포한 뒤 다시 검사하세요.", "manual_deploy", "recheck")
    if feature == "native_load":
        return result(CheckState.AVAILABLE, "컴포넌트 파일을 대조했습니다; 수동 관리를 사용할 수 있습니다.")
    if probe.core_available is not True:
        return result(CheckState.MISSING if probe.core_available is False else CheckState.WAITING,
                      "수집 컴포넌트가 없습니다." if probe.core_available is False else "수집 컴포넌트 호환성을 점검하는 중입니다.", "recheck")
    if probe.game_running is False:
        return result(CheckState.WAITING, "게임 시작을 기다리고 있습니다.", "recheck")
    if probe.game_running is None:
        return result(CheckState.WAITING, "게임 프로세스 상태가 아직 확인되지 않았습니다. 다시 검사하세요.", "recheck")
    if probe.native_diagnostic:
        return FeatureCheck(
            feature, label, (
                CheckState.WAITING_LOGIN
                if probe.native_diagnostic_state == CheckState.WAITING_LOGIN else CheckState.WAITING
            ),
            '이번 검사가 완료되지 않았습니다; 공통 실패 원인은 "네이티브 연결 및 기능 검사"를 참조하세요. 이 항목이 단독으로 고장 확인된 것은 아닙니다.',
            ("recheck",), facts + (("inspection_incomplete", True),),
        )
    if native.pipe is not True:
        return result(CheckState.WAITING, "게임 내 컴포넌트가 파이프를 생성하기를 기다리고 있습니다.", "recheck")
    if native.handshake is False:
        return result(CheckState.FAULT, "파이프는 존재하지만, 핸드셰이크가 호환성 검사를 통과하지 못했습니다.", "recheck")
    if native.handshake is None:
        return result(CheckState.WAITING, "파이프는 존재하며, 실제 핸드셰이크 점검을 기다리고 있습니다.", "recheck")
    if native.supported is None:
        return result(CheckState.WAITING, "핸드셰이크를 통과했습니다. 이 항목의 기능을 점검하는 중입니다.", "recheck")
    if native.supported is False:
        return result(CheckState.MISSING, "현재 컴포넌트가 이 항목의 기능을 제공하지 않습니다. 호환 컴포넌트 버전을 확인하세요.", "recheck")
    if native.reason in _NATIVE_DATA_FAULTS:
        return result(CheckState.FAULT, "이 항목의 데이터 또는 프로토콜 검증에 실패했습니다. 호환 컴포넌트를 확인하고 다시 검사하세요.", "recheck")
    if native.ready is not True and native.reason in {"not_ready", "source_changed"}:
        if feature == "native_inventory":
            return result(CheckState.WAITING_LOGIN, _LOGIN_WAIT_DETAIL, "recheck")
        return result(CheckState.WAITING, "이 영역이 아직 준비되지 않았거나 새로고침 중 출처가 변경되었습니다. 다시 검사해 주세요.", "recheck")
    if native.ready is not True and native.reason in (
        NATIVE_CAPTURE_TRANSIENT_REASONS | {"sdk_unavailable", "hook_unavailable"}
    ):
        message = native_capture_readiness_message(NteCoreRpcError({
            "code": -32001, "message": "not_ready", "data": {"reason": native.reason},
        }))
        state = (CheckState.WAITING_LOGIN if native.reason in {
            "world_unavailable", "controller_unavailable", "pawn_unavailable",
        } else CheckState.WAITING if native.reason in NATIVE_CAPTURE_TRANSIENT_REASONS else CheckState.FAULT)
        if state == CheckState.WAITING_LOGIN:
            message += "; 로그인하여 게임 장면에 진입한 후 다시 검사하세요."
        return result(state, message, "recheck")
    if feature == "native_inventory" and native.projection_complete is True:
        return result(CheckState.AVAILABLE, "이번 전체 가방 필드를 검증했습니다; 저장 진행 상황은 홈 화면의 가방 동기화를 참조하세요.")
    if feature == "native_equipment" and native.projection_complete is not None:
        if native.reason == "equipment_check_required":
            return result(CheckState.WAITING, "백그라운드 동기화는 장착 인터페이스를 반복 검사하지 않습니다; 다시 검사를 클릭해 대조할 수 있으며, 실제 장착 전에도 다시 확인합니다.", "recheck")
        if native.ready is True:
            return result(CheckState.AVAILABLE, "네이티브 장비 인터페이스가 준비되었습니다; 장착은 최근 저장된 완전한 가방을 사용하니, 작업 후 결과를 확인하세요.")
        return result(CheckState.WAITING, "네이티브 장비 인터페이스가 준비되기를 기다리고 있습니다.", "recheck")
    if feature == "native_character" and native.profile_projection_supported is True and native.ready is True:
        return result(CheckState.AVAILABLE, "컴포넌트가 지원하는 캐릭터 상태 필드를 동기화할 수 있습니다; 관측되지 않은 필드는 기존 값을 유지합니다.")
    observation_details = {
        "native_team": "컴포넌트가 지원하는 파티 관측을 확보했습니다; 캐릭터 객체와 장비 인스턴스의 완전한 대응 관계가 아직 확인되지 않아 완전한 실제 파티로 볼 수 없습니다.",
        "native_environment": "컴포넌트가 지원하는 환경 관측을 확보했습니다; 관측되지 않은 월드 레벨, 스테이지, 하프 또는 효과 발동 상태는 다른 증거로 보완해야 합니다.",
    }
    if feature in observation_details:
        if native.snapshot is not True:
            return result(CheckState.WAITING, "이 항목의 관측 데이터를 아직 얻지 못했습니다. 다시 검사해 주세요.", "recheck")
        if native.ready is not True:
            return result(CheckState.WAITING, native.reason or "이 항목의 관측이 아직 준비되지 않았습니다. 다시 검사하세요.", "recheck")
        return result(CheckState.AVAILABLE, observation_details[feature])
    if needs_snapshot and (native.complete is False or (
        native.snapshot is True and native.source_coverage != "complete"
    )):
        return result(CheckState.WAITING_LOGIN, "현재 일부 관찰만 있으며 아직 완전한 데이터를 얻지 못했습니다. " + _LOGIN_WAIT_DETAIL, "recheck")
    if needs_snapshot and native.snapshot is not True:
        return result(CheckState.WAITING_LOGIN, "완전한 데이터 스냅샷을 기다리는 중입니다. " + _LOGIN_WAIT_DETAIL, "recheck")
    if needs_snapshot and native.complete is not True:
        return result(CheckState.WAITING_LOGIN, "데이터를 수신했지만 완전성은 아직 확인되지 않았습니다. " + _LOGIN_WAIT_DETAIL, "recheck")
    if native.ready is not True:
        return result(CheckState.WARNING, "컴포넌트가 이 항목의 기능을 선언했지만 아직 사용할 준비가 되지 않았습니다; 잠시 후 다시 검사하세요.", "recheck")
    return result(CheckState.AVAILABLE, "이 기능에 필요한 조건이 준비되었습니다.")


def build_work_mode_report(
    settings: WorkModeSettings, probe: WorkModeProbe, *, deployment_record: dict | None = None,
) -> WorkModeReport:
    allowed = allowed_capabilities(settings)
    items = [FeatureCheck("local", "로컬 계산 및 저장된 데이터", CheckState.AVAILABLE, "로컬 계산과 저장된 데이터를 사용할 수 있습니다.")]
    items.append(FeatureCheck(
        "history_analysis", "과거 전투 리포트 분석",
        CheckState.AVAILABLE if probe.analysis_available is True else (
            CheckState.MISSING if probe.analysis_available is False else CheckState.WAITING),
        "호환 분석 컴포넌트를 대조했습니다. 과거 전투 리포트를 오프라인으로 분석할 수 있습니다." if probe.analysis_available else
        "호환 분석 컴포넌트가 없습니다; 완전한 Calc 컴포넌트 패키지를 설치한 후 다시 검사하세요." if probe.analysis_available is False else
        "분석 컴포넌트 점검이 아직 완료되지 않았습니다. 다시 검사하세요.", ("recheck",),
    ))
    if Capability.NATIVE_LOAD in allowed:
        items.append(FeatureCheck(
            "component_update", "자동 배포 및 업데이트",
            probe.component_update_state or CheckState.WAITING,
            probe.component_update_detail or "연동 컴포넌트 패키지 점검을 기다리고 있습니다.", ("recheck",),
        ))
    if settings.pending_cleanup:
        items.append(FeatureCheck(
            "cleanup", "게임 경로 확인" if probe.game_path_valid is False else "게임 디렉터리 정리",
            probe.cleanup_state or CheckState.WAITING,
            probe.cleanup_detail or (
                "게임이 실행 중입니다. 종료 후 관리된 컴포넌트를 다시 확인하고 정리합니다."
                if probe.game_running else "이 프로그램이 관리하는 컴포넌트의 점검을 기다리는 중이며, 이미 존재하지 않으면 정리된 것으로 간주합니다."
            ), ("detect_game_path", "recheck") if probe.game_path_valid is False else ("recheck",),
        ))
    if Capability.INTERFACE_INPUT in allowed:
        items.append(FeatureCheck(
            "interface_input", "마우스 및 게임패드 인터페이스 조작",
            CheckState.WAITING if probe.input_available is None else CheckState.MISSING if probe.input_available is False else (
                CheckState.AVAILABLE if probe.game_running else CheckState.WAITING
            ), "화면 입력이 준비되었습니다." if probe.input_available and probe.game_running else (
                "게임 시작을 기다리고 있습니다." if probe.input_available else
                "화면 입력 의존성이 없습니다." if probe.input_available is False else "화면 입력 조건을 아직 확인하지 않았습니다."
            ), ("recheck",),
        ))
    if Capability.PACKET_CAPTURE in allowed:
        npcap_state = (CheckState.AVAILABLE if probe.npcap_available is True else
                       CheckState.MISSING if probe.npcap_available is False else CheckState.WAITING)
        npcap_detail = (
            "Npcap 설치가 감지되었습니다; 패킷 캡처가 정상 시작되는지는 아래 패킷 캡처 동기화 상태를 확인해 주세요." if probe.npcap_available is True else
            'Npcap이 감지되지 않았습니다. 저위험 패킷 캡처에 이 컴포넌트가 필요합니다. 아래 "Npcap 다운로드"를 클릭해 공식 설치 프로그램을 실행하고,'
            '설치가 완료되면 여기로 돌아와 "다시 검사"를 클릭하세요.' if probe.npcap_available is False else
            "Npcap 검사 결과를 아직 얻지 못했습니다. 다시 검사해 주세요."
        )
        items.append(FeatureCheck("npcap", "Npcap 패킷 캡처 의존성", npcap_state, npcap_detail,
                                  ("download_npcap", "recheck") if probe.npcap_available is False else ("recheck",)))
        state, detail, actions = CheckState.AVAILABLE, "패킷 캡처와 전체 스냅샷이 준비되었습니다.", ()
        if probe.packet_fault:
            state, detail, actions = CheckState.FAULT, probe.packet_fault, ("recheck",)
        elif probe.core_available is not True:
            state = CheckState.MISSING if probe.core_available is False else CheckState.WAITING
            detail, actions = "수집 컴포넌트가 없습니다." if probe.core_available is False else "수집 컴포넌트가 아직 점검되지 않았습니다.", ("recheck",)
        elif probe.npcap_available is not True:
            state, detail, actions = npcap_state, "패킷 캡처 의존성이 아직 준비되지 않았습니다. 위의 Npcap 검사 결과에 따라 처리하세요.", ("recheck",)
        elif not probe.packet_listening:
            state, detail, actions = CheckState.WAITING, "패킷 캡처 모니터링 시작 대기 중; 로그인 전에 모니터링을 시작해야 합니다.", ("recheck",)
        elif probe.game_running is not True:
            state, detail = CheckState.WAITING, "수신 대기가 시작되었습니다. 게임 프로세스가 준비되기를 기다립니다."
        elif not probe.logged_in:
            state, detail = CheckState.WAITING_LOGIN, "수신 대기가 시작되었습니다. 로그인해 게임에 진입한 뒤 완전한 데이터를 기다리세요."
        elif not probe.packet_snapshot:
            state, detail = CheckState.WAITING_LOGIN, "완전한 로그인 데이터 대기 중; 늦게 시작한 경우 다시 로그인해야 할 수 있으며, 증분은 완전한 것으로 간주되지 않습니다."
        items.append(FeatureCheck("packet_capture", "패킷 캡처 동기화", state, detail, actions, (
            ("npcap", probe.npcap_available), ("listening", probe.packet_listening),
            ("snapshot", probe.packet_snapshot),
        )))
    if probe.native_diagnostic and Capability.NATIVE_SYNC in allowed:
        items.append(FeatureCheck(
            "native_connection", "네이티브 연결 및 업무 검사", probe.native_diagnostic_state or CheckState.FAULT,
            probe.native_diagnostic, ("recheck",),
            (("game_running", probe.game_running), ("core_available", probe.core_available)),
        ))
    for capability, label, needs_snapshot in (
        (Capability.NATIVE_LOAD, "게임 내 컴포넌트 파일", False),
        (Capability.NATIVE_BATTLE, "DLL 전투 리포트", False),
        (Capability.NATIVE_EQUIPMENT, "DLL 장비 세팅", True),
    ):
        if capability in allowed:
            items.append(_native_check(capability.value, label, getattr(probe, capability.value), probe,
                                       needs_snapshot=needs_snapshot,
                                       external_provider_allowed=settings.mode == WorkMode.DEVELOPER))
    if Capability.NATIVE_SYNC in allowed:
        for feature, label in (
            ("native_character", "DLL 캐릭터 상태 (지원되는 필드)"),
            ("native_inventory", "DLL 전체 가방 인벤토리"),
            ("native_team", "DLL 파티 관측 (지원되는 필드)"),
            ("native_environment", "DLL 환경 관측 (지원되는 필드)"),
        ):
            items.append(_native_check(feature, label, getattr(probe, feature), probe, needs_snapshot=True,
                                       external_provider_allowed=settings.mode == WorkMode.DEVELOPER))
    return WorkModeReport(
        settings.mode, tuple(items),
        sync_enable_ready=decide_sync_enable(settings, deployment_record or {}, probe).ready,
    )
