# 使用官方 SQLite 图纸约束构建并展示本地图纸方案。
"""Blueprint page that owns its widgets and request worker."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.context import AppContext
from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.features.blueprints.controller import BlueprintController
from src.features.blueprints.dependencies import BlueprintDependencies
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.services.blueprint_service import (
    OFFICIAL_SHAPE_LABELS as _OFFICIAL_SHAPE_LABELS,
)
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import match_pinyin

__all__ = ["BlueprintPage"]


class BlueprintPage:
    """Own blueprint page state and delegate generation to BlueprintController."""

    def __init__(
        self,
        *,
        app_context: AppContext,
        navigate: Callable[[str], None],
    ) -> None:
        self._app_context = app_context
        self._navigate = navigate
        self._widget: QScrollArea | None = None
        self._search: QLineEdit | None = None
        self._status: QLabel | None = None
        self._content_layout: QVBoxLayout | None = None
        self._data: dict[str, dict] = {}
        self._controller: BlueprintController | None = None
        self._worker: WorkerThread | None = None

    def build(self) -> QScrollArea:
        if self._widget is not None:
            return self._widget
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        return_button = QPushButton("← 캐릭터로 돌아가기")
        return_button.setObjectName("returnToRoleButton")
        return_button.clicked.connect(lambda: self._navigate("my_role"))
        header.addWidget(return_button)
        self._search = QLineEdit()
        self._search.setPlaceholderText("캐릭터 청사진 검색 (한글·병음 지원)…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.filter)
        header.addWidget(self._search, 1)
        refresh_button = QPushButton("청사진 생성")
        refresh_button.setObjectName("btnAction")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        layout.addLayout(header)
        self._status = QLabel(
            "청사진은 로컬 솔버가 생성합니다. 공식 캐릭터는 배포 템플릿을, 사용자 정의 캐릭터는 현재 계정에 저장된 보드와 기본 세트를 읽습니다."
        )
        self._status.setStyleSheet(themed_style("color:#8b949e"))
        layout.addWidget(self._status)
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignTop)
        layout.addWidget(content)
        layout.addStretch()
        self._widget = scroll
        return scroll

    def refresh(self) -> None:
        content_layout, status = self._require_widgets()
        self._clear_content(content_layout)
        status.setText("공식·사용자 정의 캐릭터 청사진 생성 중…")
        content_layout.addWidget(QLabel("드라이브를 조합하고 보드를 푸는 중…"))
        dependencies = BlueprintDependencies.from_app_context(self._app_context)
        controller = BlueprintController(dependencies)
        worker = WorkerThread(target=controller.generate, parent=self._widget)
        self._controller = controller
        self._worker = worker
        worker.result_ready.connect(
            lambda data: self._render(data, controller)
        )
        worker.error.connect(
            lambda error: self._render_error(error, controller)
        )
        worker.finished.connect(self._release_worker)
        worker.start()

    def filter(self, text: str) -> None:
        self._draw(text)

    def reset_account_state(self) -> None:
        self._data = {}
        if self._content_layout is not None:
            self._clear_content(self._content_layout)
        if self._status is not None:
            self._status.setText("계정이 전환되었습니다. 캐릭터 청사진을 다시 생성하세요.")

    def _render_error(
        self,
        error: str,
        controller: BlueprintController,
    ) -> None:
        if controller.accepts(
            BlueprintDependencies.from_app_context(self._app_context)
        ):
            _, status = self._require_widgets()
            status.setText(f"청사진 생성 실패: {error}")

    def _render(
        self,
        data: dict[str, dict],
        controller: BlueprintController,
    ) -> None:
        if not controller.accepts(
            BlueprintDependencies.from_app_context(self._app_context)
        ):
            return
        self._data = data or {}
        plan_count = sum(
            len(entry["blueprints"]) for entry in self._data.values()
        )
        _, status = self._require_widgets()
        status.setText(
            f"캐릭터 {len(self._data)}명의 청사진 방안 {plan_count}개를 생성했습니다."
        )
        self._draw()

    def _draw(self, filter_text: str = "") -> None:
        content_layout, _ = self._require_widgets()
        self._clear_content(content_layout)
        if not self._data:
            content_layout.addWidget(
                QLabel("청사진을 생성할 캐릭터가 없습니다. “청사진 생성”을 클릭하세요.")
            )
            return
        search_text = filter_text.strip()
        matched_roles = [
            role_name
            for role_name in self._data
            if not search_text or match_pinyin(role_name, search_text)
        ]
        show_all_for = (
            matched_roles[0]
            if search_text and len(matched_roles) == 1
            else None
        )
        shown = 0
        for role_name, role_data in sorted(self._data.items()):
            if search_text and not match_pinyin(role_name, search_text):
                continue
            shown += 1
            content_layout.addWidget(
                self._build_role_group(
                    role_name,
                    role_data,
                    show_all=role_name == show_all_for,
                )
            )
        if not shown:
            content_layout.addWidget(QLabel("일치하는 캐릭터 청사진이 없습니다."))

    def _build_role_group(
        self,
        role_name: str,
        role_data: dict,
        *,
        show_all: bool,
    ) -> QGroupBox:
        blueprints = role_data["blueprints"]
        group = QGroupBox(
            f"{role_name}  —  {role_data['suit_name']}  (청사진 {len(blueprints)}개)"
        )
        group.setStyleSheet(
            themed_style(
                "QGroupBox{font-size:13px;font-weight:600;color:#58a6ff;"
                "border:1px solid #21262d;border-radius:8px;padding-top:16px}"
            )
        )
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)
        visible = blueprints if show_all else blueprints[:4]
        plans_grid = QGridLayout()
        plans_grid.setHorizontalSpacing(16)
        plans_grid.setVerticalSpacing(10)
        for index, blueprint in enumerate(visible, start=1):
            plans_grid.addWidget(
                self._build_plan_card(index, blueprint),
                (index - 1) // 2,
                (index - 1) % 2,
            )
        group_layout.addLayout(plans_grid)
        hidden_count = len(blueprints) - len(visible)
        if hidden_count > 0:
            more = QLabel(
                f"상위 4개 청사진만 표시하며, 가능한 방안 {hidden_count}개는 표시하지 않았습니다."
            )
            more.setStyleSheet(
                themed_style("color:#8b949e;font-size:11px")
            )
            group_layout.addWidget(more)
        return group

    @staticmethod
    def _build_plan_card(index: int, blueprint: dict) -> QWidget:
        plan_card = QWidget()
        row = QHBoxLayout(plan_card)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(
            PuzzleBoardWidget(blueprint["board"], cell_size=28),
            0,
            Qt.AlignTop,
        )
        extras = QWidget()
        extras_layout = QVBoxLayout(extras)
        extras_layout.setContentsMargins(0, 0, 0, 0)
        extras_layout.setSpacing(2)
        extras_layout.addWidget(QLabel(f"방안 {index} · 추가 형태"))
        image_row = QHBoxLayout()
        image_row.setSpacing(4)
        for shape_id in blueprint.get("extra_pieces", [])[:3]:
            shape_label = _OFFICIAL_SHAPE_LABELS.get(
                str(shape_id),
                str(shape_id),
            )
            image = QLabel()
            image.setPixmap(warehouse_shape_pixmap(shape_label, "Gold"))
            image.setToolTip(shape_label)
            image.setFixedSize(52, 52)
            image.setScaledContents(True)
            image_row.addWidget(image)
        image_row.addStretch()
        extras_layout.addLayout(image_row)
        row.addWidget(extras, 1)
        return plan_card

    def _require_widgets(self) -> tuple[QVBoxLayout, QLabel]:
        if self._content_layout is None or self._status is None:
            raise RuntimeError("blueprint page has not been built")
        return self._content_layout, self._status

    @staticmethod
    def _clear_content(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _release_worker(self) -> None:
        self._worker = None
