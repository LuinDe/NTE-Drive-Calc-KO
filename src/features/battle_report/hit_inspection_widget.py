# 逐击概览按乘区、参与方和实际贡献打开详情，只展示采集与核心已有结果。
from __future__ import annotations

import json

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.battle_hit_buff_explanation_service import battle_buff_property_label
from .hit_formula_overview import HitFormulaOverview


ATTRIBUTE_NAMES = {
    "hp": "현재 생명력", "maxHp": "최대 HP", "shield": "보호막", "attack": "攻击力",
    "defense": "防御力", "crit": "暴击率", "critDamage": "暴击伤害", "mag": "环合强度",
    "damageUpGeneral": "범용 피해 증가", "chargeCurrent": "현재 충전", "chargeMax": "충전 상한",
    "unbalCurrent": "현재 브레이크", "unbalMax": "브레이크 상한", "unbalIntensity": "倾陷强度",
    "unbalAccrueEfficiency": "브레이크 누적 효율", "unbalAntiAccrueEfficiency": "브레이크 누적 저항 효율",
    "unbalSpeed": "브레이크 속도", "unbalBonus": "브레이크 보너스", "unbalReduceNatur": "브레이크 자연 감소",
    "unbalValueAdd": "브레이크 수치 부가", "isBalancedingState": "브레이크 관련 상태",
}
for _element, _name in (
    ("Normal", "일반"), ("Cosmos", "光"), ("Nature", "灵"), ("Incantation", "咒"),
    ("Chaos", "暗"), ("Psyche", "魂"), ("Lakshana", "相"), ("Psychically", "心灵"),
):
    ATTRIBUTE_NAMES[f"resist{_element}"] = f"{_name} 저항"
    ATTRIBUTE_NAMES[f"damageUp{_element}"] = f"{_name} 피해 증가"


def shown(value):
    if value is None:
        return "미획득"
    if isinstance(value, bool):
        return "예" if value else "아니요"
    return f"{value:.8g}" if isinstance(value, (int, float)) else str(value)


def native_hit(hit):
    if hit.native_evidence is None:
        return {}
    try:
        value = json.loads(hit.native_evidence.payload_json)
        raw = value.get("rawHit", {})
        return raw if isinstance(raw, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def term_side(term):
    """Use source identity, not the factor containing both participants."""
    if (term.term_id.startswith("target:") or term.source_group == "target"
            or term.property_id.startswith(("Resistance:", "DamageResist"))):
        return "victim"
    return "formula"


def term_source(term, projection=None):
    if term.source_group == "buff" and projection is not None:
        decisions = [d for d in projection.decisions if d.interval_id in term.term_id]
        return contribution_source(any(d.interval_id.startswith("native-applied:") for d in decisions), decisions)
    return {
        "native_panel": "실측 속성", "native_contribution": "실측 기여",
        "counterfactual_panel": "증거 추론", "panel_inclusion_inferred": "추정 (포함 관계)",
        "character": "동결된 캐릭터 설정", "resolved": "코어 해석값",
        "target": "대상 설정/추론", "buff": "Buff 기여 (출처는 이 히트 Buff 참고)",
    }.get(term.source_group, "동결 출처/핵심 결과")


def contribution_source(direct, decisions):
    if direct:
        return "실측 기록값 적용"
    applied = [d for d in decisions if d.status == "applied"]
    observed = [d for d in applied if d.observed_stacks is not None]
    if observed:
        return "상태 증거 있음/기여 모델" if len(observed) == len(applied) else "일부 상태에 증거/기여 모델 있음"
    return "모델 추론 기여"


class HitInspectionWidget(QWidget):
    """One display component for the analysis and counterfactual detail dialogs."""

    technical_requested = Signal()

    def __init__(self, parent=None, *, game_ui_asset_root=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(15)
        self.overview = HitFormulaOverview(self, game_ui_asset_root=game_ui_asset_root)
        self.overview.participant_requested.connect(self.participant)
        self.overview.factor_requested.connect(self.factor)
        self.layout.addWidget(self.overview)
        footer = QHBoxLayout()
        self.buff_button = QPushButton("이번 히트 Buff 기여")
        self.buff_button.clicked.connect(self.contributions)
        self.evidence_button = QPushButton("공식 값 증거")
        self.evidence_button.clicked.connect(self.formula_observations)
        self.technical_button = QPushButton("전체 공식과 원본 필드")
        self.technical_button.clicked.connect(self.technical_requested)
        for button in (self.buff_button, self.evidence_button):
            footer.addWidget(button)
        footer.addStretch(1)
        footer.addWidget(self.technical_button)
        self.layout.addLayout(footer)
        self._popup = None

    def set_hit(self, hit, replay=None, projection=None, *, participant_names=None, target_resolutions=()):
        self.hit, self.replay, self.projection = hit, replay, projection
        self.raw = native_hit(hit)
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
            self._popup = None
        self.overview.set_hit(hit, replay, self.raw, participant_names=participant_names, target_resolutions=target_resolutions)
        self.evidence_button.setVisible(bool((self.raw.get("executionEvidence") or {}).get("applicationEvidence")))

    def table(self, title, headers, rows, note=""):
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
        dialog = QDialog(self.window())
        dialog.setWindowTitle(title)
        root = QVBoxLayout(dialog)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(label)
        table = QTableWidget(len(rows), len(headers), dialog)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem(shown(value))
                item.setToolTip(shown(value))
                table.setItem(r, c, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.resizeColumnsToContents()
        limits = (200, 85, 140, 70, 110, 190) if len(headers) == 7 else (300, 130, 170)
        for column, limit in enumerate(limits[:len(headers) - 1]):
            table.setColumnWidth(column, min(table.columnWidth(column), limit))
        table.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()
        root.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.hide)
        root.addWidget(close)
        fit_dialog_to_available_screen(dialog, QSize(1050, 580))
        self._popup = dialog
        dialog.show()

    def factor(self, factor):
        rows = [(t.label or t.property_id, t.value, t.source_name or t.source_group,
                 t.evidence_basis) for t in factor.terms]
        note = f"{factor.formula}\n이 곱연산 구간 결과: {shown(factor.value)}\n{factor.evidence_basis}"
        if factor.factor_id == "critical" and self.replay.critical_state in {"non_critical", "not_applicable"}:
            note = f"이번 히트는 치명타 후보를 채택하지 않아 곱연산 구간에 1을 적용했습니다. 아래에는 치명타 후보의 배율과 출처가 그대로 남아 있습니다.\n{note}"
        self.table(factor.label, ["입력 항목", "결과 값", "출처", "근거"], rows, note)

    def participant(self, side):
        raw = self.raw
        snapshot = raw.get(f"{side}Attributes") or {}
        title = {"attacker": "공격 측 능력치", "victim": "피격자 속성", "formula": "공식 속성 측"}[side]
        rows = []
        # Identity, status and original getter source are carried with each sample.
        matched = (side != "formula" and snapshot.get("actorIndex") == raw.get(f"{side}ObjectIndex")
                   and snapshot.get("actorSerial", 0) > 0)
        for key, field in snapshot.get("values", {}).items():
            known = matched and field.get("status") == "ok" and field.get("value") is not None
            rows.append((ATTRIBUTE_NAMES.get(key, key), field.get("value") if known else None,
                         "실측" if known else "미획득", field.get("source", "")))
        if side == "victim":
            rows.extend((label, value, "기록값", basis) for label, value, basis in (
                ("정산 전 HP", self.hit.target_hp_before, "전투 리포트 원시 생명력 증거"),
                ("정산 후 생명", self.hit.target_hp_after, "전투 리포트 원시 생명력 증거"),
                ("HP 상한", self.hit.target_max_hp, "전투 리포트 원시 생명력 증거"),
            ) if value is not None)
        if self.replay is not None:
            for factor in self.replay.factors:
                if side == "attacker" and self.replay.formula_panel_character_id not in (0, None, self.hit.character_id):
                    continue
                rows.extend((f"공식 입력 · {t.label or t.property_id}", t.value,
                             term_source(t, self.projection), t.evidence_basis or t.source_name)
                            for t in factor.terms if (side == "victim") == (term_side(t) == "victim"))
        self.table(title, ["属性", "원본 수치", "출처 유형", "근거"], rows,
                   f"관측 단계: {snapshot.get('stage', '未取得')}; 관측 번호: {snapshot.get('id', '未取得')}.\n"
                   "동적 관측은 게임 원시 단위로 표시됩니다. 공식 입력은 계산 코어에서 가져옵니다. 제3자 속성을 얻지 못한 경우 공격 측 속성을 빌려 쓰지 않습니다.")

    def contributions(self):
        execution = self.raw.get("executionEvidence") or {}
        applications = execution.get("applicationEvidence") or {}
        rows = []
        identity = execution.get("identity") or {}
        for item in applications.get("items", []):
            if item.get("source") != "native_application":
                continue
            asc = item.get("recipientAsc") or {}
            weak = [asc.get("objectIndex"), asc.get("objectSerial")]
            side = "공격 측" if weak == identity.get("sourceAsc") else "피격자" if weak == identity.get("targetAsc") else "기타 대상"
            relation = {"included": "포함됨", "excluded": "미포함"}.get(item.get("inclusion"), "포함 관계 미정")
            usage = "공식 읽기 관측됨" if item.get("formulaUse") == "observed" else "공식 읽기 아직 관측되지 않음"
            buff = item.get("buff") or {}
            property_name = battle_buff_property_label(str(item.get("property") or ""))
            rows.append((f"네이티브 속성 기록 · {property_name}", side, item.get("property"),
                         item.get("value"), "실측 기록", "원본 적용 증거 (아래 채택 값과 중복 합산되지 않음)",
                         f"{relation}; {usage}; 중첩 수 {shown(item.get('stacks'))}; {item.get('operation', '')};"
                         f"원본 효과 식별자: {buff.get('className') or buff.get('name') or '未取得'}"))
        if self.projection is not None:
            terms = [t for f in (() if self.replay is None else self.replay.factors) for t in f.terms]
            for modifier in self.projection.modifiers:
                decisions = [d for d in self.projection.decisions if d.interval_id in modifier.interval_ids]
                direct = any(i.startswith("native-applied:") for i in modifier.interval_ids)
                measured = any(t.source_group == "native_panel" and
                               (t.property_id == modifier.property_id or
                                t.term_id == "native:attack" and modifier.property_id in {"AtkBase", "AtkUp", "AtkAdd"} or
                                t.term_id == "native:maxHp" and modifier.property_id in {"HPMaxBase", "HPMaxUp", "HPMaxAdd"})
                               and (term_side(t) == "victim") == (modifier.target_scope == "target") for t in terms)
                referenced = any(any(i in t.term_id for i in modifier.interval_ids) for t in terms)
                has_extra = any(t.term_id.startswith("native-extra:") and t.value != 0 for t in terms)
                usage = ("이 속성은 실측 기준과 이번 히트 기여도로 해석됩니다; 포함 관계는 곱연산 구간을 참고하세요" if measured and has_extra else
                         "실측 패널이 해당 속성을 이미 커버합니다; 소스 분해는 설명/반사실 분석에 사용됩니다" if measured else
                         "공식 입력으로 사용" if referenced else "현재 공식은 단독으로 사용되지 않습니다; 모델 연관만 유지합니다")
                rows.append(("、".join(modifier.buff_names),
                             {"self": "공격/공식 능력치 측", "target": "피격자", "team": "파티"}.get(modifier.target_scope, modifier.target_scope),
                             modifier.property_id, modifier.additive_value, contribution_source(direct, decisions), usage,
                             "；".join(reason for d in decisions for reason in d.reasons)
                             or f"코어 투영; 신뢰도 {modifier.confidence}"))
        self.table("이번 히트 Buff 기여", ["Buff", "적용 대상", "属性", "기여도", "출처", "계산 용도", "적용 근거"], rows,
                   "위의 실측 기록과 코어 채택값은 서로 다른 두 관점이므로, 둘을 다시 합산해서는 안 됩니다.\n"
                   f"현재 직접 증거 범위: {applications.get('coverage', '未取得')};"
                   f"상태: {applications.get('status', '未取得')}.\n"
                   "적용 목록이 모든 버프를 포괄하지는 않습니다. 실측 패널과 각 버프의 출처 분해는 서로 다른 증거입니다;"
                   "모델 연관이 이 히트에 실측 적용되었음을 의미하지는 않습니다. 자세한 내용은 계산 용도를 참고하세요.")

    def formula_observations(self):
        execution = self.raw.get("executionEvidence") or {}
        evidence = execution.get("applicationEvidence") or {}
        bound = (self.raw.get("executionId") is not None
                 and str(self.raw["executionId"]) == str(execution.get("executionId")))
        rows = []
        for term in evidence.get("baseTerms", []):
            component = term.get("component") or {}
            identity = f"컴포넌트 {component.get('objectIndex')}:{component.get('objectSerial')}"
            witness = (bound and term.get("returned") is True and term.get("identityStable") is True
                       and term.get("attackReadPathObserved") is True)
            for key, label in (("usedAttack", "실제 읽은 공격력"), ("result", "기본 항 반환값")):
                field = term.get(key) or {}
                known = field.get("status") == "ok" and (witness if key == "usedAttack" else bound and term.get("returned"))
                rows.append((identity, label, field.get("value") if known else None,
                             "클라이언트 실측" if known else "읽기 증거 없음", "계산 인스턴스와 읽기 경로 연관"))
            for phase, title in (("before", "기본 항목 함수 진입"), ("after", "기본 항 함수 반환")):
                inputs = term.get(phase) or {}
                attack = inputs.get("attack") or {}
                rows.append((title, "집계 공격력", attack.get("value"), "로컬 속성 관측", identity))
                for name in ("AtkBase", "AtkUp", "AtkAdd", "OnlineAtkRatio"):
                    values = inputs.get(name) or {}
                    for part, caption in (("base", "기본값"), ("current", "현재 값")):
                        field = values.get(part) or {}
                        rows.append((title, f"{name} · {caption}", field.get("value"),
                                     "로컬 속성 관측", "서버 동시점 값 미확인" if name == "OnlineAtkRatio" else identity))
        self.table("클라이언트 공식 값 증거", ["단계/대상", "필드", "값", "출처", "근거"], rows,
                   "여기에는 클라이언트의 실제 함수 호출이 표시됩니다. 기본 항목 반환값은 서버의 최종 피해가 아닙니다; 속성 기록과 패널 포함 관계는 각각 별도로 판단합니다.")
