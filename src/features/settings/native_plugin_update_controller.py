# 在后台执行显式组件更新，界面只解释前置条件和已确认的操作结果。
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox

from src.integrations.native_capture_process import native_game_pid
from src.integrations.operation_guard import bind_execution_guard


class _UpdateWorker(QThread):
    result_ready = Signal(object)

    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work
        self.result = None

    def run(self):
        try:
            self.result = self.work()
        except Exception as error:
            self.result = error
        self.result_ready.emit(self.result)


class NativePluginUpdateController(QObject):
    changed = Signal()

    def __init__(self, *, context, policy, session, loader, generation, maintenance, parent):
        super().__init__(parent)
        self.context, self.policy, self.session = context, policy, session
        self.loader, self.generation = loader, generation
        self.worker = None
        self.maintenance = maintenance
        self._stopping = False
        self._result_handled = False

    @property
    def running(self):
        return self.worker is not None or self.maintenance.uncertain

    def request(self):
        if self.running:
            return
        owner = self.parent()
        try:
            self.policy.require('native_load')
            pid = native_game_pid()
            if pid is None:
                raise RuntimeError('게임이 아직 실행되지 않아 핫 업데이트할 수 있는 실행 중인 플러그인이 없습니다.\n'
                                   '게임을 시작하기 전에 컴포넌트를 업데이트하려면 “네이티브 컴포넌트 배포”를 클릭하세요. '
                                   '핫 업데이트하려면 먼저 게임을 시작한 뒤 이 버튼을 클릭하세요.')
            settings = self.policy.settings
            if self.policy.deployment_record.get('loading_method') == 'loader':
                workspace = self.loader.native_workspace_record
                if workspace is None:
                    raise RuntimeError('Loader 실행 디렉터리 기록이 없습니다.')
                directory = workspace.directory
            else:
                directory = Path(settings.game_executable).parent
            if not directory.is_absolute():
                raise RuntimeError('게임 컴포넌트 디렉터리가 아직 확인되지 않았습니다.')
        except Exception as error:
            QMessageBox.warning(owner, '자체 플러그인 업데이트', str(error))
            return
        if QMessageBox.question(owner, '자체 플러그인 업데이트',
            'Calc와 게임은 열어 둔 채로 진행할 수 있습니다.\n동기화, HUD, 성능 표시가 잠시 일시 중지되며, 업데이트 후 현재 켜기/끄기 설정에 따라 다시 연결합니다.'
            '\n전투 리포트 녹화나 게임 쓰기 작업이 진행 중이면 강제로 업데이트하지 않습니다. D3D 호스트가 바뀐 경우에는 여전히 게임을 종료해야 합니다.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        worker = _UpdateWorker(None, self)
        guard = bind_execution_guard(self.policy.require, should_stop=worker.isInterruptionRequested,
                                     generation=self.generation)
        root = self.context.paths.root
        try:
            self.maintenance.begin()
        except Exception as error:
            worker.deleteLater()
            QMessageBox.warning(owner, '자체 플러그인 업데이트', str(error))
            return

        def work():
            return self.maintenance.run(application_root=root, directory=directory,
                                        game_pid=pid, operation_guard=guard)

        worker.work = work
        self.worker = worker
        self._stopping = False
        self._result_handled = False
        self._target_directory = directory
        self._request_generation = self.generation()
        worker.result_ready.connect(self._result)
        worker.finished.connect(self._finished)
        worker.start()
        self.changed.emit()

    def _result(self, result):
        if self._result_handled:
            return
        self._result_handled = True
        current = self.generation() == self._request_generation and not self._stopping
        try:
            if not isinstance(result, Exception) and result.managed_files:
                record = dict(self.policy.deployment_record)
                path_key = 'native_workspace_root' if record.get('loading_method') == 'loader' else 'workspace_path'
                recorded_directory = Path(record.get(path_key) or record.get('game_executable', '')).resolve()
                if recorded_directory.name.lower() == 'htgame.exe':
                    recorded_directory = recorded_directory.parent
                if recorded_directory != self._target_directory.resolve():
                    raise RuntimeError('플러그인은 업데이트되었지만 배포 디렉터리 기록이 변경되었습니다. 컴포넌트를 확인한 뒤 기능을 복구하세요.')
                key = 'native_workspace_files' if record.get('loading_method') == 'loader' else 'managed_files'
                record[key] = {**record.get(key, {}), **result.managed_files}
                record['deployment_layout'] = result.layout
                self.policy.update_deployment(record)
        except Exception as error:
            self.maintenance.uncertain = True
            result = error
        self.maintenance.finish(restore=current)
        if not current:
            return
        if isinstance(result, Exception):
            detail = str(result)
            if self.maintenance.uncertain:
                detail += '\n업데이트 또는 등록이 확인되지 않아 관련 네이티브 기능은 일시 중지 상태로 유지됩니다. 업데이트를 반복하지 말고 확인한 뒤 복구하세요.'
            QMessageBox.warning(self.parent(), '자체 플러그인 업데이트', detail)
        else:
            QMessageBox.information(self.parent(), '자체 플러그인 업데이트', result.detail +
                                    '\n기능 스케줄링을 재개했으며 현재 켜기/끄기 설정에 따라 다시 연결하는 중입니다. 각 기능의 준비 여부는 해당 상태를 확인하세요.')

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        self.changed.emit()

    def stop(self):
        self._stopping = True
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait()
            self._result(self.worker.result)
