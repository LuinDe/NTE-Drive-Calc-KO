# 按同步来源引导重连，区分组件部署、抓包准备、就绪与同步完成。
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.home.page import inventory_sync_error_guidance


class SyncRetryDialog(QDialog):
    def __init__(self, parent, *, controller, native=False):
        super().__init__(parent)
        self.controller = controller
        self.native = native
        self._begun = False
        self._ready = False
        self._invalid = False
        self.setWindowTitle("게임 데이터 동기화 재시작" if native else "가방 동기화 재시작")
        layout = QVBoxLayout(self)
        self.detail = QLabel(
            (
                "동기화 이상이 있거나 데이터가 갱신되지 않을 때는 여기서 동기화 연결을 다시 설정할 수 있습니다.\n\n"
                "컴포넌트를 배포하거나 업데이트해야 하면 먼저 게임을 완전히 종료하고, 배포가 완료된 후 게임을 시작하세요."
                '로그인하여 게임 씬에 진입한 후 "동기화 재시작 시작"을 클릭하고, 동기화가 완료될 때까지 기다리세요.'
            ) if native else (
                "동기화 이상이 있거나 데이터가 갱신되지 않을 때는 여기서 동기화 연결을 다시 설정할 수 있습니다.\n\n"
                '먼저 게임 로그인 화면으로 돌아간 후 "동기화 재시작 시작"을 클릭하세요;'
                "준비가 완료되기를 기다린 후, 게임에 다시 로그인하세요."
            ),
            self,
        )
        self.detail.setWordWrap(True)
        self._instructions = self.detail.text()
        layout.addWidget(self.detail)
        self.buttons = QHBoxLayout()
        self.begin = QPushButton("동기화 재시작 시작", self)
        self.begin.clicked.connect(self._start)
        self.dismiss = QPushButton("닫기", self)
        self.dismiss.clicked.connect(self._dismiss)
        self.buttons.addWidget(self.begin)
        self.buttons.addWidget(self.dismiss)
        layout.addLayout(self.buttons)
        controller.state_changed.connect(self.update_state)
        controller.preparation_changed.connect(self.update_preparation)
        self.update_preparation(controller.preparation_state)
        fit_dialog_to_available_screen(self, QSize(500, 165))

    def _start(self):
        if self._invalid or self._begun:
            return
        if self.controller.window.battle_report_controller.is_running():
            self.detail.setText("먼저 전투 리포트를 종료한 후 동기화를 다시 시작하세요.")
            return
        self._begun = True
        self.begin.setEnabled(False)
        self.dismiss.setText("재시작 취소")
        self.controller.restart()
        self.update_preparation(self.controller.preparation_state)

    def update_preparation(self, preparation):
        if self._invalid:
            return
        if not self._begun:
            self._ready = False
            self.detail.setText(self._instructions)
            self.begin.setText("동기화 재시작 시작")
            return
        messages = {
            'checking_game': (
                "게임 상태와 컴포넌트 배포 상황을 확인하는 중입니다." if self.native
                else "게임 상태를 확인하는 중입니다. 로그인 화면에 머물러 주세요."
            ),
            'waiting_game_exit': "컴포넌트의 배포 또는 업데이트가 아직 완료되지 않았습니다. 게임을 완전히 종료하고, 배포가 완료된 후 다시 시작하여 게임 장면에 진입하세요.",
            'waiting_deployment': "컴포넌트의 배포 또는 업데이트가 아직 완료되지 않았으니, 당분간 게임을 시작하지 마세요. 검사 상세 정보를 확인하고, 배포를 완료한 후 게임을 시작하여 게임 장면에 진입하세요.",
            'waiting_game': (
                "게임 시작을 기다리고 있습니다. 로그인하여 게임 장면에 진입하면, 프로그램이 가방과 캐릭터 데이터를 자동으로 동기화합니다." if self.native
                else "게임 시작을 기다리고 있습니다. 시작 후에는 먼저 로그인 화면에 머무르고, 준비가 완료된 후에 로그인하세요."
            ),
            'waiting_component': "게임이 감지되어 동기화 컴포넌트 준비를 기다리는 중입니다. 게임 씬에 진입해 주세요; 오랫동안 준비되지 않으면 검사 상세 정보를 확인해 주세요.",
            'stopping': "기존 동기화 연결을 종료하는 중이며, 이후 자동으로 다시 연결됩니다.",
        }
        if preparation in messages:
            self._ready = False
            if self._begun:
                self.dismiss.setText("재시작 취소")
            self.detail.setText(messages[preparation])
            return
        if self._begun:
            service = self.controller.window._inventory_sync_service
            if service is not None:
                self.update_state(service.state)
            else:
                self.detail.setText("가방과 캐릭터 데이터 읽기를 준비하는 중입니다. 게임 화면으로 들어가 동기화가 완료될 때까지 기다려 주세요." if self.native else "모니터링을 준비하는 중입니다. 잠시 로그인 화면에 머물러 주세요.")

    def update_state(self, state):
        if not self._begun or self._invalid:
            return
        preparation = self.controller.preparation_state
        if preparation is not None and state.phase != "error":
            self.update_preparation(preparation)
            return
        if state.phase == "error":
            guidance = inventory_sync_error_guidance(
                state.error_code, state.error,
                capture_source="native" if self.native else "packet",
            ).replace("처리:", "다음 단계:")
            self.detail.setText("상태: 재시작 동기화가 완료되지 않았지만 저장된 가방은 계속 사용할 수 있습니다.\n" + guidance)
            self.detail.setToolTip(
                f"오류 코드: {state.error_code or '未分类'}; 자세한 원인은 검사 상세 정보 또는 계정 로그를 확인하세요."
            )
            self._begun = False
            self.begin.setEnabled(True)
            self.begin.setText("다시 시도")
            self.dismiss.setText("닫기")
        elif state.source_snapshot_ready and state.phase == "listening":
            self._ready = True
            self.detail.setText("데이터 동기화 완료, 백그라운드 수신 대기가 재개되었습니다.")
            self.dismiss.setText("완료")
        elif state.phase in {"collecting", "saving"}:
            self._ready = True
            self.detail.setText("데이터를 읽고 저장하는 중입니다. 완료 후 백그라운드 모니터링을 계속합니다.")
            self.dismiss.setText("닫기")
        elif state.capturing and not self.native:
            self._ready = True
            self.detail.setText("동기화 연결 준비가 완료되었습니다. 이제 게임에 다시 로그인해 주세요.\n\n데이터 읽기와 저장이 완료되면 백그라운드 모니터링을 계속합니다.")
            self.dismiss.setText("닫기")
        else:
            self.detail.setText(
                state.message + "\n\n게임 장면에 진입해 동기화가 끝날 때까지 기다리세요." if self.native
                else state.message + "\n\n로그인 화면에 머무르며 준비가 끝날 때까지 기다리세요."
            )

    def context_changed(self):
        self._invalid = True
        self.reject()

    def _dismiss(self):
        if self._begun and not self._ready and not self._invalid:
            self.controller.cancel_restart()
        self.reject()
