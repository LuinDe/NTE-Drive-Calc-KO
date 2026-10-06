# 以无音效的普通弹窗展示工作台功能入口与简短操作说明。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen


@dataclass(frozen=True)
class GuideSection:
    title: str
    lines: tuple[str, ...]
    numbered: bool = False


@dataclass(frozen=True)
class FeatureGuide:
    title: str
    entry: str
    sections: tuple[GuideSection, ...]
    destinations: tuple[tuple[str, str], ...]


FEATURE_GUIDES = (
    FeatureGuide(
        "기본 계산", "계산",
        (
            GuideSection("", (
                "먼저 선택한 위험 모드에 따라 데이터를 가져오세요.",
                "「계산」에서 캐릭터 우선순위와 분배 설정을 선택하세요.",
                "「계산 시작」을 클릭해 결과를 확인하세요; 마음에 들면 「장비 세팅 저장」을 클릭하세요.",
            ), True),
            GuideSection("저위험", ("작업 공간-가방 동기화(권장) 또는 계산-스캔 모드를 사용하세요.",)),
            GuideSection("중위험", ("작업 공간-게임 데이터 동기화(권장)를 사용하세요. 가방 동기화보다 실시간으로 동기화할 수 있습니다.",)),
        ),
        (("계산으로 이동", "execute"),),
    ),
    FeatureGuide(
        "장비 세팅", "장비 세팅",
        (
            GuideSection("", (
                "「계산」에서 방안을 저장해 두었는지 확인하세요.",
                "「장비 세팅」에서 캐릭터와 슬롯을 선택하고 「계산 세팅」과 「게임 세팅」을 확인하세요.",
                "게임 내 장비가 필요하면 오른쪽 위에서 사용 가능한 장착 방식을 선택하세요.",
            ), True),
            GuideSection("저위험", ("「자동 장착」을 사용할 수 있으며, 시간이 더 걸립니다.",)),
            GuideSection("중위험", ("「고속 장착」을 사용할 수 있으며, 빠르고 편리합니다 (추천).",)),
        ),
        (("장비 세팅으로 이동", "equipment"),),
    ),
    FeatureGuide(
        "폐기 잠금", "저위험 계산, 중위험 창고 (권장)",
        (
            GuideSection("저위험", (
                "「계산」의 첫 단계에서 전체 스캔을 선택하고 「관리」를 클릭하세요.",
                "「관리」에서 정상, 잠금 또는 폐기 기준을 선택하세요 (켜는 것을 잊지 마세요).",
                "목표를 확인한 후 저장하세요. 스캔 완료 후 폐기 잠금이 천천히 진행됩니다.",
            ), True),
            GuideSection("중위험", (
                '「창고」에서 콘솔을 선택해 상태를 "폐기/잠금/정상"으로 변경할 수 있습니다.',
                "오른쪽 위 「관리」에서 정상, 잠금 또는 폐기 기준을 선택하세요 (켜는 것을 잊지 마세요).",
                "목표를 확인한 후 저장하세요. 게임 내 콘솔에서 폐기 잠금이 빠르게 완료됩니다.",
            ), True),
        ),
        (("계산으로 이동", "execute"), ("창고로 이동", "warehouse")),
    ),
    FeatureGuide(
        "콘솔 감정", "감정",
        (GuideSection("", (
            "콘솔 유형과 품질을 선택하세요.",
            "이미지 선택, 붙여넣기, 스크린샷 또는 수동으로 속성을 입력하세요.",
            "「이미지 분석」 또는 「감정 시작」을 클릭해 속성과 점수를 확인하세요.",
        ), True),),
        (("감정으로 이동", "identify"),),
    ),
    FeatureGuide(
        "되감기", "도구 → 되감기 추천",
        (GuideSection("", (
            "목표 캐릭터, 추천 방식, 목표 단계를 선택하세요.",
            "「방안 생성」을 클릭해 각 슬롯의 추천을 확인하세요.",
            "확인 후 「방안 저장」을 사용할 수 있습니다; 되감기를 맡기려면 「되감기 진행」을 이어서 선택하세요.",
        ), True),),
        (("도구로 이동", "toolbox"),),
    ),
    FeatureGuide(
        "한계 이득", "전투 리포트 → 감사 → 한계 이득 계산",
        (GuideSection("", (
            "「전투 리포트」에서 분석 가능한 저장된 전투 리포트를 하나 여세요.",
            "「감사」에서 「한계 이득 계산」을 클릭하고, 분석할 캐릭터를 선택한 뒤 후보 구성을 조정하세요.",
            "「재계산」을 클릭해 현재와 후보의 피해를 비교하세요; 결과는 고정 축 추정치이며, 실측 전투 리포트는 변경하지 않습니다.",
        ), True),),
        (("전투 리포트로 이동", "battle_report"),),
    ),
)


class FeatureGuideDialog(QDialog):
    """Use a plain QDialog rather than an audible system message box."""

    def __init__(self, parent: QWidget, guide: FeatureGuide, navigate: Callable[[str], None]):
        super().__init__(parent)
        self.setObjectName("featureGuideDialog")
        self.setWindowTitle(guide.title + " · 기능 설명")
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

        heading = QLabel(guide.title, self)
        heading.setObjectName("featureGuideDialogTitle")
        heading.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#f0f6fc"))
        layout.addWidget(heading)
        entry = QLabel("진입 경로:" + guide.entry, self)
        entry.setObjectName("featureGuideEntry")
        entry.setWordWrap(True)
        layout.addWidget(entry)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(10)
        for section in guide.sections:
            if section.title:
                title = QLabel(section.title + "：", content)
                title.setStyleSheet(themed_style("font-weight:700;color:#58a6ff"))
                content_layout.addWidget(title)
            for index, line in enumerate(section.lines, 1):
                label = QLabel(f"{index}. {line}" if section.numbered else line, content)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
                content_layout.addWidget(label)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        for text, key in guide.destinations:
            button = QPushButton(text, self)
            button.clicked.connect(lambda _checked=False, target=key: self._navigate(navigate, target))
            actions.addWidget(button)
        close = QPushButton("닫기", self)
        close.setDefault(True)
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        fit_dialog_to_available_screen(self, QSize(650, 480 if guide.title == "폐기 잠금" else 360))

    def _navigate(self, navigate: Callable[[str], None], key: str) -> None:
        self.accept()
        navigate(key)


def add_feature_guides(layout: QVBoxLayout, parent: QWidget, navigate: Callable[[str], None]) -> None:
    """Show six small buttons on one row without executing the feature."""
    row = QHBoxLayout()
    row.setSpacing(10)
    for guide in FEATURE_GUIDES:
        button = QPushButton(guide.title, parent)
        button.setObjectName("featureGuideButton")
        button.setAccessibleName(guide.title + "설명")
        button.setFixedWidth(88)
        button.clicked.connect(
            lambda _checked=False, item=guide: FeatureGuideDialog(parent, item, navigate).exec()
        )
        row.addWidget(button)
    row.addStretch()
    layout.addLayout(row)
