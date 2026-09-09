# 展示目标原始逐击、最大生命变化与派生结算证据。
"""Target-vital evidence tables for one immutable battle analysis snapshot."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisSnapshot
from src.features.battle_report.analysis_components import analysis_table
from src.features.battle_report.target_condition_selector import (
    BattleTargetConditionSelector,
)
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox


_RESISTANCE_LABELS = (
    ("normal", "일반 저항"),
    ("chaos", "암 저항"),
    ("cosmos", "빛 저항"),
    ("incantation", "주 저항"),
    ("lakshana", "상 저항"),
    ("nature", "령 저항"),
    ("psyche", "혼 저항"),
    ("psychically", "정신 저항"),
)


def _number(value: float) -> str:
    return f"{value:,.0f}"


def _optional_number(value: float | None) -> str:
    return "—" if value is None else _number(value)


def _time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def _half_label(value: str) -> str:
    return {"upper": "전반", "lower": "후반"}.get(value.casefold(), "전체")


class BattleTargetVitalPanel(QWidget):
    condition_save_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.environment_dialog = QDialog(self)
        self.environment_dialog.setWindowTitle("이번 전투 환경 설정")
        self.environment_dialog.setModal(True)
        dialog_layout = QVBoxLayout(self.environment_dialog)
        self.condition_selector = BattleTargetConditionSelector(
            self.environment_dialog
        )
        self.condition_selector.preset_changed.connect(self._apply_preset)
        dialog_layout.addWidget(self.condition_selector)
        self._selection_metadata: dict[str, object] = {}
        condition_grid = QGridLayout()
        condition_grid.setHorizontalSpacing(8)
        condition_grid.setVerticalSpacing(8)
        condition_grid.addWidget(QLabel("대상 이름"), 0, 0)
        self.target_name_edit = QLineEdit()
        condition_grid.addWidget(self.target_name_edit, 0, 1)
        condition_grid.addWidget(QLabel("적 레벨"), 0, 2)
        self.enemy_level_spin = self._number_spin(1.0, 999.0, decimals=0)
        condition_grid.addWidget(self.enemy_level_spin, 0, 3)
        condition_grid.addWidget(QLabel("장면"), 0, 4)
        self.scene_combo = NoWheelComboBox()
        self.scene_combo.addItem("轨外之境", "outer_realm")
        self.scene_combo.addItem("大世界", "open_world")
        condition_grid.addWidget(self.scene_combo, 0, 5)
        condition_grid.addWidget(QLabel("적 방어력 감소"), 0, 6)
        self.defense_reduction_spin = self._percent_spin(-100.0, 100.0)
        condition_grid.addWidget(self.defense_reduction_spin, 0, 7)
        condition_grid.addWidget(QLabel("적 취약"), 0, 8)
        self.vulnerability_spin = self._percent_spin(-100.0, 1000.0)
        condition_grid.addWidget(self.vulnerability_spin, 0, 9)
        condition_grid.addWidget(QLabel("실제 DefBase"), 3, 0)
        self.enemy_defense_base_spin = self._number_spin(
            0.0,
            1_000_000_000.0,
            decimals=2,
        )
        self.enemy_defense_base_spin.setSpecialValueText("레벨 기준 근사")
        condition_grid.addWidget(self.enemy_defense_base_spin, 3, 1)
        condition_grid.addWidget(QLabel("DefUp"), 3, 2)
        self.enemy_defense_up_spin = self._percent_spin(-100.0, 1000.0)
        condition_grid.addWidget(self.enemy_defense_up_spin, 3, 3)
        condition_grid.addWidget(QLabel("DefAdd"), 3, 4)
        self.enemy_defense_add_spin = self._number_spin(
            -1_000_000_000.0,
            1_000_000_000.0,
            decimals=2,
        )
        condition_grid.addWidget(self.enemy_defense_add_spin, 3, 5)
        condition_grid.addWidget(QLabel("UnbalMax"), 3, 6)
        self.enemy_topple_limit_spin = self._number_spin(
            0.0,
            1_000_000.0,
            decimals=2,
        )
        condition_grid.addWidget(self.enemy_topple_limit_spin, 3, 7)
        self.resistance_spins: dict[str, NoWheelDoubleSpinBox] = {}
        for index, (damage_type, label) in enumerate(_RESISTANCE_LABELS):
            row = 1 + index // 4
            column = (index % 4) * 2
            condition_grid.addWidget(QLabel(f"{label} (전투 전)"), row, column)
            editor = self._percent_spin(-500.0, 500.0)
            self.resistance_spins[damage_type] = editor
            condition_grid.addWidget(editor, row, column + 1)
        self.save_condition_button = QPushButton("환경 설정 저장")
        self.save_condition_button.setObjectName("btnPrimary")
        self.save_condition_button.clicked.connect(self._request_condition_save)
        self.save_condition_button.clicked.connect(self.environment_dialog.accept)
        condition_grid.addWidget(self.save_condition_button, 2, 8, 1, 2)
        dialog_layout.addLayout(condition_grid)
        self.condition_note = QLabel()
        self.condition_note.setWordWrap(True)
        self.condition_note.setStyleSheet(
            themed_style("color:#d29922;font-size:12px")
        )
        dialog_layout.addWidget(self.condition_note)
        dialog_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        dialog_buttons.rejected.connect(self.environment_dialog.reject)
        dialog_layout.addWidget(dialog_buttons)
        self.target_table = analysis_table(
            (
                "하프",
                "대상",
                "인스턴스",
                "히트",
                "정식 히트",
                "상한 감소",
                "상한 정산 피해",
                "설명 예상",
                "분석 유효 피해",
                "관측 HP 소모",
                "미설명 차액",
                "최초 HP",
                "종료 HP",
                "원본 최대 HP",
            ),
            190,
            default_widths=(
                70,
                145,
                185,
                70,
                110,
                110,
                130,
                120,
                110,
                110,
                110,
                110,
                120,
                130,
            ),
        )
        layout.addWidget(self.target_table)
        self.event_table = analysis_table(
            (
                "시간",
                "대상",
                "귀속",
                "메커니즘",
                "최대 HP 변화",
                "정산 전 HP 비율",
                "유효 피해",
                "근거",
                "신뢰도",
            ),
            210,
            default_widths=(
                110,
                145,
                110,
                245,
                180,
                150,
                120,
                110,
                150,
            ),
        )
        layout.addWidget(self.event_table)
        self.note = QLabel(
            "HP 상한 정산 = 변화 전 같은 하프·같은 인스턴스·같은 이전 상한의 인근 히트 중 최소 "
            "HPAfter ÷ 이전 HPMax × HPMax 감소량입니다. 정식 히트는 덮어쓰지 않으며,"
            "관측 HP 소모 = 분석 유효 피해 + 미설명 차액입니다."
        )
        self.note.setWordWrap(True)
        self.note.setStyleSheet(themed_style("color:#d29922;font-size:12px"))
        layout.addWidget(self.note)

    def clear(self) -> None:
        self.target_table.setRowCount(0)
        self.event_table.setRowCount(0)
        self.condition_note.setText("아직 적 조건을 불러오지 않았습니다.")

    def set_catalog(self, catalog: dict[str, object]) -> None:
        self.condition_selector.set_catalog(catalog)

    def open_environment_dialog(self) -> None:
        fit_dialog_to_available_screen(
            self.environment_dialog,
            QSize(1040, 720),
        )
        self.environment_dialog.open()

    def render(
        self,
        analysis: BattleAnalysisSnapshot,
        *,
        projected_time: Callable[[int], int],
    ) -> None:
        condition = analysis.target_condition
        self.condition_selector.render(
            condition,
            detected_environment_kind=getattr(
                analysis,
                "detected_environment_kind",
                "",
            ),
            detected_environment_ref=getattr(
                analysis,
                "detected_environment_ref",
                "",
            ),
            detected_difficulty_id=getattr(
                analysis,
                "detected_environment_difficulty_id",
                None,
            ),
            detected_options=getattr(
                analysis,
                "detected_environment_options",
                (),
            ),
            detected_floor=getattr(
                analysis,
                "detected_outer_realm_floor",
                None,
            ),
        )
        self._selection_metadata = self.condition_selector.current_preset()
        suggested_name = next(
            (
                target.target_name
                for target in analysis.targets
                if target.target_name != "未知目标"
            ),
            "단일 대상 (확인 대기)",
        )
        self.target_name_edit.setText(
            condition.target_name if condition is not None else suggested_name
        )
        self.enemy_level_spin.setValue(
            condition.enemy_level if condition is not None else 90.0
        )
        scene = condition.scene if condition is not None else "outer_realm"
        self.scene_combo.setCurrentIndex(max(0, self.scene_combo.findData(scene)))
        self.defense_reduction_spin.setValue(
            (condition.defense_reduction if condition is not None else 0.0) * 100.0
        )
        self.vulnerability_spin.setValue(
            (condition.vulnerability if condition is not None else 0.0) * 100.0
        )
        self.enemy_defense_base_spin.setValue(
            condition.enemy_defense_base
            if condition is not None and condition.enemy_defense_base is not None
            else 0.0
        )
        self.enemy_defense_up_spin.setValue(
            (condition.enemy_defense_up if condition is not None else 0.0) * 100.0
        )
        self.enemy_defense_add_spin.setValue(
            condition.enemy_defense_add if condition is not None else 0.0
        )
        self.enemy_topple_limit_spin.setValue(
            condition.enemy_topple_limit if condition is not None else 50.0
        )
        resistances = dict(condition.resistances) if condition is not None else {}
        for damage_type, editor in self.resistance_spins.items():
            editor.setValue(float(resistances.get(damage_type, 0.20)) * 100.0)
        self.condition_note.setText(
            (
                "사용자가 확인한 단일 대상 조건을 저장했습니다. 전투 전 저항에는 모드 추가/약점이 포함되며,"
                "전투 중의 임시 저항 감소는 포함되지 않습니다. 실제 DefBase가 0이 아니면 바인딩된 속성 팩을 우선 사용하고,"
                "그렇지 않을 때만 레벨과 장면 기준으로 근사합니다. 취약 기본값은 0입니다."
                if condition is not None
                else "현재 전투 리포트에 대상 인스턴스/몬스터 ID가 없습니다. 위의 전투 전 저항 20%는"
                "편집 초기값일 뿐이며, 저장하기 전에는 적 매개변수가 필요한 계산에 참여하지 않습니다. 전투 중의"
                "임시 저항 감소를 중복 입력하지 마세요."
            )
        )
        self.target_table.setRowCount(len(analysis.targets))
        for row, target in enumerate(analysis.targets):
            values = (
                _half_label(target.scope_half),
                target.target_name,
                (
                    "— (인스턴스 ID 없음)"
                    if target.target_id == "unknown"
                    else target.target_id
                ),
                f"{target.hits:,}",
                _number(target.damage),
                _number(target.max_hp_reduction),
                _number(target.max_hp_reduction_damage),
                _number(target.estimated_max_hp_reduction_damage),
                _number(target.effective_damage),
                _number(target.observed_hp_loss),
                _number(target.unexplained_hp_delta),
                _optional_number(target.initial_hp),
                _optional_number(target.terminal_hp),
                _optional_number(target.max_hp),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 10:
                    item.setToolTip(
                        "양수는 관측 HP 소모가 설명된 피해보다 여전히 높다는 뜻이고, 음수는 분석 피해가"
                        "관측 HP 소모보다 높다는 뜻으로, 오버킬·회복 상쇄·동시 샘플·복합 이벤트에서 흔히 나타납니다."
                        "이 차액은 장부를 맞추는 데만 쓰이며 캐릭터나 스킬에 배분하지 않습니다."
                    )
                self.target_table.setItem(row, column, item)

        events = tuple(
            sorted(
                (*analysis.max_hp_events, *analysis.estimated_max_hp_events),
                key=lambda event: (event.observed_at_us, event.event_id),
            )
        )
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = (
                _time(projected_time(event.observed_at_us)),
                event.target_name,
                event.source_character_name,
                event.mechanic_name,
                f"{_number(event.old_max_hp)} → {_number(event.new_max_hp)}",
                f"{event.hp_ratio_before * 100:.2f}%",
                _number(event.effective_hp_loss),
                (
                    "실측 변화"
                    if event.included_in_effective_damage
                    else "설명 예상"
                ),
                f"귀속{event.attribution_confidence} / 정산{event.calculation_confidence}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {0, 4, 5, 6}:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 3:
                    item.setToolTip(event.inference_basis)
                self.event_table.setItem(row, column, item)
        identity_note = {
            "instance_scoped": (
                "대상 인스턴스 ID 완전: 최대 HP 상태를 하프와 인스턴스별로 각각 유지합니다. 정식 히트는 덮어쓰지 않으며,"
                "관측 HP 소모·분석 유효 피해·미설명 차액은 엄격히 일치하며, 차액은 캐릭터에 배분하지 않습니다."
            ),
            "mixed_guarded": (
                "대상 인스턴스 ID 일부 누락: 식별 가능한 인스턴스만 최대 HP 파생에 참여하고 알 수 없는 대상 행은 제외하여"
                "대상 간 혼합을 방지합니다."
            ),
            "single_target_assumed": (
                "이 기록에는 대상 인스턴스 ID가 없습니다: 현재 단일 대상 가정에 따라 “알 수 없는 대상” 한 행으로 병합했습니다."
                "실제 전투에 적이 여럿이었다면 이 페이지의 HP 상한 정산은 신뢰할 수 있는 결과로 볼 수 없습니다."
            ),
            "user_confirmed_single_target": (
                "이 기록에는 정식 대상 인스턴스 ID가 없습니다. 사용자가 대상을 하나만 선택한다고 명시했으므로"
                "분석 투영이 적을 향한 모든 히트를 해당 대상에 바인딩했습니다. 이 신원은 사용자 근거에 속하며"
                "HP 근거 그룹화에만 쓰이고, nte-core 원본 인스턴스 ID로 기록되거나 이를 대신하지 않으며,"
                "새 히트의 적 속성에 대한 주요 대상 폴백으로도 쓰이지 않습니다."
            ),
        }.get(
            analysis.target_identity_mode,
            "HP 상한 도출에 사용할 수 있는 정식 대상 HP 샘플이 없습니다.",
        )
        estimate_note = (
            f" 설명 예상 합계 {_number(analysis.estimated_max_hp_reduction_damage)},"
            "낮은 신뢰도의 약한 근거이므로 기본적으로 유효 피해에 포함하지 않습니다."
            if analysis.estimated_max_hp_events
            else ""
        )
        self.note.setText(identity_note + estimate_note)

    @staticmethod
    def _number_spin(
        minimum: float,
        maximum: float,
        *,
        decimals: int,
    ) -> NoWheelDoubleSpinBox:
        editor = NoWheelDoubleSpinBox()
        editor.setDecimals(decimals)
        editor.setRange(minimum, maximum)
        return editor

    @classmethod
    def _percent_spin(
        cls,
        minimum: float,
        maximum: float,
    ) -> NoWheelDoubleSpinBox:
        editor = cls._number_spin(minimum, maximum, decimals=2)
        editor.setSuffix("%")
        return editor

    def _request_condition_save(self) -> None:
        payload = {
            "target_name": self.target_name_edit.text().strip(),
            "enemy_level": self.enemy_level_spin.value(),
            "scene": self.scene_combo.currentData(),
            "enemy_defense_base": self.enemy_defense_base_spin.value() or None,
            "enemy_defense_up": self.enemy_defense_up_spin.value() / 100.0,
            "enemy_defense_add": self.enemy_defense_add_spin.value(),
            "enemy_topple_limit": self.enemy_topple_limit_spin.value(),
            "defense_reduction": self.defense_reduction_spin.value() / 100.0,
            "vulnerability": self.vulnerability_spin.value() / 100.0,
            "resistances": {
                damage_type: editor.value() / 100.0
                for damage_type, editor in self.resistance_spins.items()
            },
        }
        payload.update({
            key: self._selection_metadata.get(key)
            for key in (
                "environment_kind",
                "environment_ref",
                "environment_name",
                "selected_target_ids",
                "selected_target_profiles",
                "primary_target_id",
                "difficulty_id",
                "feast_options",
                "witch_buff_id",
                "witch_buff_name_zh",
                "witch_buff_property_id",
                "witch_buff_value",
                "witch_buff_is_percent",
            )
        })
        self.condition_save_requested.emit(payload)

    def _apply_preset(self, preset: object) -> None:
        if not isinstance(preset, dict):
            return
        self._selection_metadata = dict(preset)
        if not preset.get("target_name"):
            return
        self.target_name_edit.setText(str(preset["target_name"]))
        self.enemy_level_spin.setValue(float(preset["enemy_level"]))
        self.scene_combo.setCurrentIndex(
            max(0, self.scene_combo.findData(preset["scene"]))
        )
        self.enemy_defense_base_spin.setValue(
            float(preset.get("enemy_defense_base") or 0.0)
        )
        self.enemy_defense_up_spin.setValue(
            float(preset.get("enemy_defense_up") or 0.0) * 100.0
        )
        self.enemy_defense_add_spin.setValue(
            float(preset.get("enemy_defense_add") or 0.0)
        )
        self.enemy_topple_limit_spin.setValue(
            float(preset.get("enemy_topple_limit") or 50.0)
        )
        resistances = preset.get("resistances") or {}
        for damage_type, editor in self.resistance_spins.items():
            editor.setValue(float(resistances.get(damage_type, 0.0)) * 100.0)
        self.condition_note.setText(
            "공식 정적 카탈로그에서 현재 대상과 속성 팩을 불러왔습니다. 쟁봉 보너스는 전투 전 매개변수에 이미 적용되었습니다."
            "아래에서 직접 보정할 수 있으며, 저장을 클릭한 뒤에야 히트 리플레이에 참여합니다."
        )
