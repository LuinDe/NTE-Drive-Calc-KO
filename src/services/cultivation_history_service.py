# 协调账号养成历史的冻结保存、工作草稿绑定和删除后的写入撤销。
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4

from src.domain.cultivation_history import (
    HistoryCalculationEnvelope, HistoryConflict, HistoryContextExpired, HistoryPage,
    HistoryPayload, HistoryRecord, HistorySelection, HistorySummary, freeze_configuration,
)
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(slots=True)
class _Binding:
    mode: str
    history_id: str | None = None
    revision: int | None = None
    request_revision: int = 0
    write_epoch: int = 0
    suppressed: bool = False


class CultivationHistoryService:
    """只消费当前账号路径；DAO 在调用线程创建，连接不跨线程共享。"""

    def __init__(
        self, *, account_id: str, user_database_path: str | Path,
        context_identity: Callable[[], object],
        dao_factory: Callable[[Path], UserDataDao] = UserDataDao,
    ) -> None:
        self._account_id = account_id
        self._database_path = Path(user_database_path).resolve()
        self._identity = context_identity
        self._initial_identity = context_identity()
        self._dao_factory = dao_factory
        self._bindings: dict[str, _Binding] = {}
        self._closed = False
        # GUI draft invalidation never waits for a database busy timeout.
        self._state_lock = RLock()
        self._operations = Lock()

    def _check_context(self) -> None:
        with self._state_lock:
            if self._closed:
                raise HistoryContextExpired("육성 기록 세션이 종료되었습니다")
        try:
            identity = self._identity()
        except (OSError, RuntimeError) as error:
            raise HistoryContextExpired("육성 기록 데이터 컨텍스트가 무효화되었습니다") from error
        if identity != self._initial_identity:
            raise HistoryContextExpired("육성 기록의 계정 또는 데이터가 변경되었습니다")

    def new_session(self, mode: str) -> str:
        self._check_context()
        if mode not in {"single", "batch"}:
            raise ValueError("육성 기록 모드가 잘못되었습니다")
        session_id = uuid4().hex
        with self._state_lock:
            self._bindings[session_id] = _Binding(mode)
        return session_id

    def discard_session(self, session_id: str) -> None:
        with self._state_lock:
            self._bindings.pop(session_id, None)

    def invalidate(self, session_id: str) -> None:
        with self._state_lock:
            binding = self._bindings.get(session_id)
            if binding is not None:
                binding.request_revision += 1

    def saving_suppressed(self, session_id: str) -> bool:
        """删除后供展示层撤销旧保存提示及重试，不读库或更改绑定。"""
        self._check_context()
        with self._state_lock:
            binding = self._bindings.get(session_id)
            return binding is None or binding.suppressed

    def freeze(
        self, session_id: str, configuration: object, *, explicit_calculation: bool,
    ) -> HistoryCalculationEnvelope:
        self._check_context()
        raw = freeze_configuration(configuration)
        with self._state_lock:
            binding = self._bindings.get(session_id)
            if binding is None:
                raise HistoryContextExpired("육성 작업 초안이 종료되었습니다")
            # Only a newly submitted explicit action can reopen a deleted binding.
            if binding.suppressed and explicit_calculation:
                binding.history_id = None
                binding.revision = None
                binding.write_epoch += 1
                binding.suppressed = False
            binding.request_revision += 1
            return HistoryCalculationEnvelope(
                self._account_id, self._initial_identity, session_id, binding.request_revision,
                binding.write_epoch, raw, explicit_calculation,
            )

    def _check_envelope(self, envelope: HistoryCalculationEnvelope) -> None:
        self._check_context()
        with self._state_lock:
            binding = self._bindings.get(envelope.session_id)
            if (envelope.account_id != self._account_id
                    or envelope.context_identity != self._initial_identity
                    or binding is None or binding.suppressed
                    or binding.request_revision != envelope.request_revision
                    or binding.write_epoch != envelope.write_epoch):
                raise HistoryContextExpired("육성 기록 저장 요청이 만료되었거나 삭제되었습니다")

    def assert_current(self, envelope: HistoryCalculationEnvelope) -> None:
        """供后台投影与重试使用的公开冻结边界检查。"""
        self._check_envelope(envelope)

    def save(self, envelope: HistoryCalculationEnvelope, payload: HistoryPayload) -> HistorySummary:
        """在后台执行；重试仍使用同一信封，草稿编辑会撤销重试资格。"""
        if envelope.configuration_json != payload.configuration_json:
            raise ValueError("육성 기록 계산 입력이 결과 엔벨로프와 일치하지 않습니다")
        with self._operations:
            self._check_envelope(envelope)
            with self._state_lock:
                binding = self._bindings[envelope.session_id]
                if binding.mode != payload.mode:
                    raise ValueError("육성 기록 모드가 작업 초안과 일치하지 않습니다")
                if binding.history_id is None:
                    binding.history_id = uuid4().hex
                history_id, expected_revision = binding.history_id, binding.revision
            try:
                with self._dao_factory(self._database_path) as dao:
                    saved = dao.save_cultivation_history(
                        self._account_id, history_id, payload, expected_revision=expected_revision,
                        check=lambda: self._check_envelope(envelope),
                    )
            except HistoryConflict:
                with self._state_lock:
                    current = self._bindings.get(envelope.session_id)
                    if current is not None and current.history_id == history_id:
                        current.suppressed = True
                        current.write_epoch += 1
                raise
            with self._state_lock:
                current = self._bindings.get(envelope.session_id)
                if current is not None and current.history_id == history_id:
                    current.revision = saved.revision
            return saved

    def list(self, *, mode: str | None = None, search: str = "", page: int = 1) -> HistoryPage:
        self._check_context()
        with self._dao_factory(self._database_path) as dao:
            return dao.list_cultivation_histories(
                self._account_id, mode=mode, search=search, page=page, check=self._check_context,
            )

    def get(self, history_id: str) -> HistoryRecord | None:
        self._check_context()
        with self._dao_factory(self._database_path) as dao:
            return dao.get_cultivation_history(self._account_id, history_id, check=self._check_context)

    def select_all(self, *, mode: str | None = None, search: str = "") -> tuple[HistorySelection, ...]:
        self._check_context()
        with self._dao_factory(self._database_path) as dao:
            return dao.select_cultivation_histories(
                self._account_id, mode=mode, search=search, check=self._check_context,
            )

    def delete(self, selection: Sequence[HistorySelection]) -> int:
        frozen = tuple(selection)
        with self._operations:
            self._check_context()
            with self._dao_factory(self._database_path) as dao:
                deleted = dao.delete_cultivation_histories(
                    self._account_id, frozen, check=self._check_context,
                )
            ids = {item.history_id for item in frozen}
            with self._state_lock:
                for binding in self._bindings.values():
                    if binding.history_id in ids:
                        binding.suppressed = True
                        binding.write_epoch += 1
                        binding.request_revision += 1
            return deleted

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            self._bindings.clear()
