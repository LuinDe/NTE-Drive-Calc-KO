# 使用角色页公共组件编辑战报中的单个角色配置副本。
"""Battle build snapshot editor dialog."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.official_role.profile_editor import OfficialRoleProfileEditor
from src.features.battle_report.marginal_replacement_controller import (
    show_marginal_equipment_replacement,
)
from src.services.battle_build_equipment_service import freeze_equipment_context
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)


class BattleBuildSnapshotEditorDialog(QDialog):
    """Collect a complete counterfactual role and equipment copy."""

    ACTION_SAVE = "save"
    ACTION_IMPORT_CULTIVATION = "import_cultivation"
    ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT = (
        "import_cultivation_and_equipment"
    )
    ACTION_SAVE_AND_SYNC_CULTIVATION = "save_and_sync_cultivation"

    def __init__(
        self,
        editor_data: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("이번 전투 캐릭터 설정 사본 편집")
        self._action = ""
        self._equipment_editable = bool(editor_data.get("equipment_editable", True))
        self._equipment_assumed = bool(editor_data.get("equipment_assumed", False))
        self._details = deepcopy(list(editor_data.get("details") or ()))
        self._editors: list[OfficialRoleProfileEditor] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        note = QLabel(self._note_text())
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#58a6ff;font-weight:600"))
        layout.addWidget(note)
        if not editor_data.get("has_edit"):
            seed = QLabel(
                "처음 수정할 때 이번 전투의 원본 고정 설정을 복사했습니다. 저장하기 전에는 전투 리포트나 캐릭터 페이지가 바뀌지 않습니다."
            )
            seed.setWordWrap(True)
            seed.setStyleSheet(themed_style("color:#d29922;font-size:12px"))
            layout.addWidget(seed)

        self.tabs = QTabWidget()
        for index, detail in enumerate(self._details):
            scroll = self._editor_scroll(index)
            character = detail["character"]
            self.tabs.addTab(
                scroll,
                str(character.get("name_zh") or character["character_id"]),
            )
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.cancel_button = QPushButton("취소")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.import_cultivation_button = QPushButton(
            "캐릭터 페이지에서 동기화 (콘솔·드라이브 제외)"
        )
        self.import_cultivation_button.setToolTip(
            "현재 캐릭터 페이지의 육성으로 전투 리포트 수정 사본을 덮어쓰고, 사본에서 선택한 콘솔/드라이브는 유지합니다."
        )
        self.import_cultivation_button.clicked.connect(
            self._import_cultivation
        )
        actions.addWidget(self.import_cultivation_button)
        self.import_all_button = QPushButton(
            "캐릭터 페이지에서 동기화 (콘솔·드라이브 포함)"
        )
        self.import_all_button.setToolTip(
            "현재 캐릭터 페이지의 육성과 현재 콘솔/드라이브로 전투 리포트 수정 사본을 덮어씁니다."
            "장비에 역으로 기록하지는 않습니다."
        )
        self.import_all_button.clicked.connect(self._import_all)
        actions.addWidget(self.import_all_button)
        self.import_all_button.setVisible(self._equipment_editable)
        actions.addStretch()
        self.save_button = QPushButton("수정 사본 저장")
        self.save_button.setObjectName("btnPrimary")
        self.save_button.clicked.connect(self._save_only)
        actions.addWidget(self.save_button)
        self.save_and_sync_button = QPushButton(
            "저장 후 캐릭터 페이지에 동기화 (콘솔·드라이브 제외)"
        )
        self.save_and_sync_button.setToolTip(
            "육성 설정만 캐릭터 페이지에 동기화합니다. 선택한 콘솔/드라이브는 전투 리포트 사본에만 저장됩니다."
        )
        self.save_and_sync_button.clicked.connect(self._save_and_sync)
        actions.addWidget(self.save_and_sync_button)
        layout.addLayout(actions)
        fit_dialog_to_available_screen(self, QSize(1080, 820))

    def profiles(self) -> list[dict]:
        profiles = []
        for editor in self._editors:
            profile = editor.profile()
            if not self._equipment_editable:
                profiles.append(profile)
                continue
            selection = editor.selected_equipment_context()
            if selection is None:
                raise ValueError("전투 리포트 캐릭터 사본에 한계 이득 장비 세팅 컨텍스트가 없습니다")
            context_key, context = selection
            profile.update(
                {
                    "equipment_context_key": context_key,
                    "equipment_context_title": str(
                        context.get("source_title")
                        or context.get("title")
                        or "전투 리포트 장비 세팅 사본"
                    ),
                    "equipment_source_kind": str(
                        context.get("source_kind") or "edited_copy"
                    ),
                    "equipment_override": freeze_equipment_context(context),
                }
            )
            profiles.append(profile)
        return profiles

    def _note_text(self) -> str:
        if self._equipment_assumed:
            return (
                "이번 전투는 완전한 원본 가방을 가져오지 못해 콘솔/드라이브에 졸업 템플릿 가정을 사용합니다. "
                "육성을 수정하고 히트별·속성 이득을 계산할 수 있습니다. 템플릿 집계 장비는 읽기 전용입니다."
            )
        if not self._equipment_editable:
            return (
                "가져온 전투 리포트입니다. 패키지에 고정된 콘솔/드라이브는 읽기 전용입니다. 레벨·각성"
                "·스킬·아크는 수정할 수 있으며 캐릭터 페이지와 육성을 단방향 동기화할 수 있습니다. 장비는 변경하지 않습니다."
            )
        return (
            "이번 전투 리포트의 캐릭터 설정 사본입니다. 저장하면 히트별 피해 리플레이·"
            "반사실 한계 이득 계산에 사용됩니다. 전투 리포트 메인 페이지의 실측 피해, DPS, 타임라인, 원본 데이터는 바뀌지 않습니다."
            "콘솔/드라이브를 선택하거나 교체할 수 있으며, 캐릭터 페이지, 인벤토리, 저장된 방안은 수정되지 않습니다."
        )

    def _editor_scroll(self, index: int) -> QScrollArea:
        detail = self._details[index]
        editor = OfficialRoleProfileEditor(
            detail,
            self,
            include_analysis=False,
            include_equipment=True,
            allow_equipment_replacement=self._equipment_editable,
            show_equipment_context_selector=self._equipment_editable,
            equipment_replacement_handler=(
                lambda target, context_key, current=index: (
                    self._replace_equipment(current, target, context_key)
                )
            ),
            scoring_engine=getattr(self.parent(), "scoring_engine", None),
            shape_areas=getattr(self.parent(), "_shape_areas", {}),
        )
        if index < len(self._editors):
            self._editors[index] = editor
        else:
            self._editors.append(editor)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(editor)
        return scroll

    def _replace_equipment(
        self,
        index: int,
        target: dict,
        context_key: str,
    ) -> bool:
        if not self._equipment_editable or not 0 <= index < len(self._details):
            return False
        detail = self._details[index]
        context = (detail.get("equipment_contexts") or {}).get(context_key)
        if not isinstance(context, dict):
            return False

        accepted = show_marginal_equipment_replacement(
            self,
            detail,
            target,
            context_key=context_key,
            on_replaced=lambda replacement: (
                BattleMarginalCandidateService.replace_equipment(
                    context,
                    target,
                    replacement,
                )
            ),
            title="이번 전투 캐릭터 설정 교체",
            summary=(
                "여기서는 현재 팝업의 초안만 수정하며, 저장을 클릭해야 이번 전투 리포트의 수정 사본에 복사됩니다."
                "캐릭터 페이지, 인벤토리, 게임의 현재 장비 세팅 또는 저장된 방안은 수정하지 않습니다."
            ),
        )
        if accepted:
            title = self.tabs.tabText(index)
            old_scroll = self.tabs.widget(index)
            self.tabs.removeTab(index)
            old_scroll.deleteLater()
            self.tabs.insertTab(index, self._editor_scroll(index), title)
            self.tabs.setCurrentIndex(index)
        return accepted

    def action(self) -> str:
        return self._action

    def _save_only(self) -> None:
        self._action = self.ACTION_SAVE
        self.accept()

    def _save_and_sync(self) -> None:
        self._action = self.ACTION_SAVE_AND_SYNC_CULTIVATION
        self.accept()

    def _import_cultivation(self) -> None:
        self._action = self.ACTION_IMPORT_CULTIVATION
        self.accept()

    def _import_all(self) -> None:
        self._action = self.ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT
        self.accept()
