# 以参与方头像、结算对照和可点击因子展示已有逐击结果，不重算伤害。
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from src.app.theme import themed_style
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.skill_name_rendering_service import battle_hit_skill_label


def factor_name(factor) -> str:
    return {"Atk 곱연산 구간": "攻击力", "HPMax 곱연산 구간": "HP 상한", "Def 곱연산 구간": "防御力",
            "배율 구간": "技能倍率", "치명 피해 배율": "치명타 배율",
            # native page labels stay Chinese (nte-analysis-core output)
            "Atk 乘区": "攻击力", "HPMax 乘区": "HP 상한", "Def 乘区": "防御力",
            "倍率区": "技能倍率", "暴击伤害倍率": "치명타 배율"}.get(factor.label, factor.label)


def formula_layout(replay):
    """Describe relationships only; diagnostic inputs are not multiplied again."""
    factors = tuple(replay.factors)
    ids = {f.factor_id for f in factors}
    contributions = tuple(f for f in factors if f.factor_id.startswith("topple_character:"))
    if contributions:
        primary = contributions
        operator = "+"
    elif {"recorded_direct_damage", "weave_followup"} <= ids:
        primary = tuple(f for f in factors if f.factor_id in {"recorded_direct_damage", "weave_followup"})
        operator = "×"
    elif {"skill", "scaling", "defense", "resistance", "vulnerability"} <= ids:
        known = {"skill", "state_coefficient", "scaling", "damage_up", "defense", "resistance",
                 "vulnerability", "independent", "dot_final", "critical"}
        primary = tuple(f for f in factors if f.factor_id in known)
        operator = "×"
    else:
        primary, operator = factors, ""
    used = {f.factor_id for f in primary}
    return primary, tuple(f for f in factors if f.factor_id not in used), operator


def factor_value(factor, critical_state: str) -> str:
    if factor.factor_id == "critical" and critical_state in {"non_critical", "not_applicable"}:
        return "1"
    return f"{factor.value:,.4f}".rstrip("0").rstrip(".")


def target_portrait_identity(hit, raw, resolutions):
    """Use existing per-target identity; do not infer from display names."""
    monster = str(raw.get("targetMonsterId") or "")
    if monster:
        return monster, "DLL 히트별 몬스터 식별자"
    for row in resolutions:
        if (row.scope_half, row.captured_target_id) != (hit.scope_half, hit.target_id):
            continue
        if row.resolved_monster_id:
            return row.resolved_monster_id, "코어가 해석한 대상 매핑"
        if row.default_monster_id:
            return row.default_monster_id, "코어가 우선 추론한 대상 프로필"
    return "", "이번 히트에 해당하는 몬스터 식별자를 획득하지 못했습니다"


def _label(text="", *, secondary=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if secondary:
        label.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
    return label


class ParticipantButton(QPushButton):
    def __init__(self, side, *, right=False, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(86)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(themed_style(
            "QPushButton{background:transparent;border:1px solid transparent;padding:4px;text-align:left;}"
            "QPushButton:hover{background:#161b22;border-color:#30363d;}"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)
        self.portrait = _label("◎")
        self.portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.portrait.setFixedSize(60, 64)
        self.portrait.setStyleSheet(themed_style("background:#161b22;border:1px solid #30363d;border-radius:8px;font-size:24px;"))
        info = QVBoxLayout()
        info.setSpacing(3)
        self.side = _label(side, secondary=True)
        self.name = _label()
        self.name.setStyleSheet("font-weight:600;font-size:14px;")
        link = _label("이 히트의 속성 보기 ›")
        link.setStyleSheet(themed_style("color:#58a6ff;font-size:11px;"))
        for label in (self.side, self.name, link):
            label.setAlignment(Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
            info.addWidget(label)
        if right:
            layout.addLayout(info, 1)
            layout.addWidget(self.portrait)
        else:
            layout.addWidget(self.portrait)
            layout.addLayout(info, 1)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_person(self, name, icon_path):
        self.name.setText(name)
        self.setAccessibleName(f"{self.side.text()} · {name}, 이 히트의 속성 보기")
        self.portrait.clear()
        image = QPixmap(str(icon_path)) if icon_path else QPixmap()
        if image.isNull():
            self.portrait.setText("◎")
        else:
            self.portrait.setPixmap(image.scaled(
                QSize(56, 60), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


class HitFormulaOverview(QWidget):
    participant_requested = Signal(str)
    factor_requested = Signal(object)

    def __init__(self, parent=None, *, game_ui_asset_root=None):
        super().__init__(parent)
        self._catalog = GameUiAssetCatalog(game_ui_asset_root) if game_ui_asset_root else None
        self._tiles = []
        self._extra_tiles = []
        self._columns = 0
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.attacker = ParticipantButton("공격 측", parent=self)
        self.victim = ParticipantButton("피격자", right=True, parent=self)
        self.attacker.clicked.connect(lambda: self.participant_requested.emit("attacker"))
        self.victim.clicked.connect(lambda: self.participant_requested.emit("victim"))
        skill = QVBoxLayout()
        self.hit_identity = _label(secondary=True)
        self.skill_name = _label()
        self.skill_name.setStyleSheet("font-size:18px;font-weight:600;")
        self.skill_meta = _label(secondary=True)
        for label in (self.hit_identity, self.skill_name, self.skill_meta):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            skill.addWidget(label)
        header.addWidget(self.attacker, 3)
        header.addLayout(skill, 3)
        header.addWidget(self.victim, 3)
        root.addLayout(header)
        self.formula_party = QPushButton()
        self.formula_party.setObjectName("formulaAttributeParty")
        self.formula_party.clicked.connect(lambda: self.participant_requested.emit("formula"))
        root.addWidget(self.formula_party)
        result = QFrame()
        result.setObjectName("hitResultStrip")
        result.setStyleSheet(themed_style(
            "QFrame#hitResultStrip{background:#161b22;border:1px solid #30363d;border-radius:9px;}"))
        result_layout = QHBoxLayout(result)
        result_layout.setContentsMargins(18, 13, 18, 13)
        self.calculated = self._stat(result_layout, "공식 피해", accent=True)
        self.observed = self._stat(result_layout, "실측 피해")
        self.delta = self._stat(result_layout, "공식 − 실측", small=True)
        root.addWidget(result)
        self.formula_title = _label("이번 히트 전체 공식")
        self.formula_title.setStyleSheet("font-weight:600;font-size:14px;")
        root.addWidget(self.formula_title)
        self.expression = _label(secondary=True)
        root.addWidget(self.expression)
        self.factor_grid = QGridLayout()
        self.factor_grid.setSpacing(9)
        root.addLayout(self.factor_grid)
        self.settlement = _label(secondary=True)
        self.settlement.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.settlement)
        self.extra_title = _label("계수 전개/계산 입력", secondary=True)
        root.addWidget(self.extra_title)
        self.extra_grid = QGridLayout()
        self.extra_grid.setSpacing(9)
        root.addLayout(self.extra_grid)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    @staticmethod
    def _stat(layout, title, *, accent=False, small=False):
        box = QVBoxLayout()
        caption = _label(title, secondary=True)
        value = _label("—")
        value.setStyleSheet(themed_style(
            f"font-size:{20 if small else 29}px;font-weight:600;" + ("color:#58a6ff;" if accent else "")))
        for label in (caption, value):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label)
        layout.addLayout(box, 1)
        return value

    def _tile(self, factor, value, operator):
        button = QPushButton()
        button.setObjectName("hitFactorTile")
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        button.setMinimumHeight(76)
        button.setStyleSheet(themed_style(
            "QPushButton#hitFactorTile{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:0;}"
            "QPushButton#hitFactorTile:hover{border-color:#58a6ff;background:#21262d;}"))
        box = QVBoxLayout(button)
        box.setContentsMargins(12, 9, 12, 9)
        box.setSpacing(3)
        name = _label(factor_name(factor), secondary=True)
        owner = _label({"skill": "스킬", "state_coefficient": "状态", "scaling": "속성 측",
                        "damage_up": "속성 측", "critical": "속성 측", "defense": "양측",
                        "resistance": "양측", "vulnerability": "피격자"}.get(factor.factor_id, "정산"), secondary=True)
        owner.setAlignment(Qt.AlignmentFlag.AlignRight)
        heading = QHBoxLayout()
        heading.addWidget(name, 1)
        heading.addWidget(owner)
        box.addLayout(heading)
        amount = _label(f"{operator} {value}".strip())
        amount.setStyleSheet("font-size:19px;font-weight:500;")
        for label in (name, owner, amount):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(amount)
        button.setAccessibleName(f"{factor_name(factor)}: {value}, 상세 보기")
        button.setToolTip(f"{factor.label}：{factor.value:.15g}\n{factor.formula}\n{factor.evidence_basis}")
        button.clicked.connect(lambda _checked=False, f=factor: self.factor_requested.emit(f))
        return button

    def set_hit(self, hit, replay, raw, *, participant_names=None, target_resolutions=()):
        catalog = self._catalog
        self.attacker.set_person(hit.character_name, catalog.character_icon(hit.character_id) if catalog else None)
        monster, portrait_basis = target_portrait_identity(hit, raw, target_resolutions)
        victim_icon = (catalog.monster_variant_icon(monster) or catalog.monster_family_icon(monster)) if catalog and monster else None
        self.victim.set_person(hit.target_name or "未知目标", victim_icon)
        self.victim.setToolTip(f"초상화 출처: {portrait_basis}" + (f"\n몬스터 식별자: {monster}" if monster else "")
                              + ("\n대응하는 아바타 리소스 없음" if monster and victim_icon is None else ""))
        self.hit_identity.setText(f"Hit #{hit.sequence} · {hit.relative_time_us / 1_000_000:.3f} s")
        self.hit_identity.setToolTip(f"히트 ID: {hit.event_id}\n스킬 식별자: {hit.ability_id or '未取得'}")
        self.skill_name.setText(battle_hit_skill_label(hit.damage_name, hit.skill_name, hit.ability_id))
        self.skill_name.setToolTip(f"스킬: {hit.skill_name}\n피해 항목: {hit.damage_name}\n히트 ID: {hit.event_id}")
        crit = "" if replay is None else {
            "critical": "暴击", "non_critical": "비치명", "not_applicable": "치명타 해당 없음",
            "ambiguous": "치명타 미정", "unreplayable": "치명타 알 수 없음",
        }.get(replay.critical_state, "치명타 알 수 없음")
        self.skill_meta.setText(" · ".join(x for x in ("" if replay is None else f"{replay.formula_type} 공식", crit) if x))
        panel = None if replay is None else replay.formula_panel_character_id
        self.formula_party.setVisible(panel not in (None, 0, hit.character_id))
        name = (participant_names or {}).get(panel, str(panel))
        self.formula_party.setText(f"공식 속성 측 · {name}    이 히트의 속성 보기 ›")
        selected = None if replay is None else replay.selected_damage
        observed = hit.damage if replay is None else getattr(replay, "observed_damage", hit.damage)
        self.calculated.setText("—" if selected is None else f"{selected:,.0f}")
        self.observed.setText(f"{observed:,.0f}")
        self.observed.setToolTip("이번 히트 공식은 관측과 비교 가능합니다; 원시 기록과 HP 차감 기준은 값 취득 증거에서 확인하세요.")
        # A display difference only; all formula results are supplied by the core.
        self.delta.setText("—" if selected is None else f"{selected - observed:+,.2f}")
        error = None if replay is None else getattr(replay, "signed_error_percent", None)
        if selected is not None and error is not None:
            self.delta.setText(f"{selected - observed:+,.2f}\n({error:+.2f}%)")
        self.delta.setToolTip("공식 − 실측" if error is None else f"실측 대비 공식: {error:+.4f}%")
        for grid in (self.factor_grid, self.extra_grid):
            while grid.count():
                item = grid.takeAt(0)
                item.widget().deleteLater()
        self._tiles, self._extra_tiles = [], []
        primary, extras, operator = ((), (), "") if replay is None else formula_layout(replay)
        if not primary:
            self.expression.setText("현재 히트에는 표시할 수 있는 구조화된 공식이 아직 없습니다; 실측 피해와 증거는 그대로 보존됩니다.")
        elif not operator:
            self.expression.setText("계산 인자는 다음과 같습니다; 현재 타입의 조합 관계는 인자 상세에서 확인하세요.")
        else:
            self.expression.setText("피해 = " + f" {operator} ".join(factor_name(f) for f in primary))
        self.formula_title.setText(f"이번 히트 전체 공식 · {len(primary)}항" if primary else "이번 히트 전체 공식")
        for i, factor in enumerate(primary):
            self._tiles.append(self._tile(factor, factor_value(factor, replay.critical_state), operator if i else ""))
        for factor in extras:
            self._extra_tiles.append(self._tile(factor, factor_value(factor, replay.critical_state), ""))
        self.extra_title.setVisible(bool(extras))
        self.settlement.setText("" if selected is None else f"코어 정산 결과 {selected:,.0f} · 각 항목을 클릭하면 전체 정밀도와 근거를 확인할 수 있습니다")
        if replay is not None and replay.critical_state in {"non_critical", "not_applicable"} and any(f.factor_id == "critical" for f in primary):
            self.settlement.setText(self.settlement.text() + "; 이번 히트에는 치명타 배율 1을 적용합니다")
        self._columns = 0
        self._reflow()

    def _reflow(self):
        columns = 4 if self.width() >= 780 else 3 if self.width() >= 620 else 2
        if columns == self._columns:
            return
        self._columns = columns
        for grid, tiles in ((self.factor_grid, self._tiles), (self.extra_grid, self._extra_tiles)):
            for index in range(4):
                grid.setColumnStretch(index, 1 if index < columns else 0)
            while grid.count():
                grid.takeAt(0)
            for index, tile in enumerate(tiles):
                grid.addWidget(tile, index // columns, index % columns)
        self.layout().invalidate()
        self.setMinimumHeight(self.layout().minimumSize().height())
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()
