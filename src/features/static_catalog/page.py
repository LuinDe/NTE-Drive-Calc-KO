# 构建工具下的游戏资料库菜单与只读领域页面。
"""Game-styled catalog menu with paged read-only domain browsing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.contracts import (
    SOURCE_LABELS,
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogDomainNavigation,
    CatalogNavigationEntry,
    CatalogRelationGroup,
    CatalogSection,
)
from src.features.static_catalog.controller import StaticCatalogController
from src.features.static_catalog.menu import StaticCatalogMenu

if TYPE_CHECKING:
    from src.services.static_catalog_mechanics_service import CatalogLink


@dataclass(frozen=True, slots=True)
class StaticCatalogDomainPageSpec:
    """One explicitly injected game-styled domain page and its owned cleanup."""

    domain_key: str
    title: str
    build: Callable[[QWidget], QWidget]
    close: Callable[[], None]
    refresh: Callable[[], None] | None = None


class StaticCatalogPage:
    """Own only search controls, selections, and discardable projections."""

    def __init__(
        self,
        *,
        controller: StaticCatalogController,
        dialog_parent: QWidget,
        game_ui_asset_root: str | Path | None = None,
        domain_pages: tuple[StaticCatalogDomainPageSpec, ...] = (),
    ) -> None:
        self._controller = controller
        self._dialog_parent = dialog_parent
        self._game_ui_asset_root = game_ui_asset_root
        self._domain_page_specs = domain_pages
        self._domain_page_specs_by_key = {
            spec.domain_key: spec for spec in domain_pages
        }
        self._domain_pages: dict[str, QWidget] = {}
        self._domain_contents: dict[str, QWidget] = {}
        self._navigation_history: list[CatalogNavigationEntry] = []
        self._navigation_buttons: list[QPushButton] = []
        self._closed = False
        self._page: QWidget | None = None
        self._stack: QStackedWidget | None = None
        self._menu: StaticCatalogMenu | None = None
        self._result_list: QListWidget | None = None
        self._search_edit: QLineEdit | None = None
        self._detail_layout: QVBoxLayout | None = None
        self._domain_title: QLabel | None = None
        self._page_label: QLabel | None = None
        self._previous_button: QPushButton | None = None
        self._next_button: QPushButton | None = None
        self._domain_labels: dict[str, str] = {}
        self._offset = 0
        self._total = 0
        self._domain_key = ""
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_search)

    def build(self) -> QWidget:
        if self._page is not None:
            return self._page
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)
        self._stack = QStackedWidget(page)
        self._menu = StaticCatalogMenu(
            game_ui_asset_root=self._game_ui_asset_root,
            parent=self._stack,
        )
        self._menu.domain_selected.connect(self._open_domain)
        self._stack.addWidget(self._menu)
        self._stack.addWidget(self._build_browser(self._stack))
        root.addWidget(self._stack, 1)
        self._page = page
        self.refresh()
        return page

    def refresh(self) -> None:
        """Start a new frozen release request and rebuild domain metadata."""

        if self._page is None:
            return
        try:
            request = self._controller.refresh_release()
        except Exception as exc:
            self._show_error("게임 자료실을 열 수 없음", exc)
            return
        provider_domains = tuple(
            domain for domain in request.domains if domain.key != "coverage"
        )
        by_key = {domain.key: domain for domain in provider_domains}
        next_order = max((domain.order for domain in provider_domains), default=0) + 1
        for index, spec in enumerate(self._domain_page_specs):
            by_key.setdefault(
                spec.domain_key,
                CatalogDomain(spec.domain_key, spec.title, "", next_order + index),
            )
        visible_domains = tuple(
            sorted(by_key.values(), key=lambda domain: domain.order)
        )
        self._domain_labels = {domain.key: domain.label for domain in visible_domains}
        if self._menu is not None:
            self._menu.set_domains(visible_domains)
        for spec in self._domain_page_specs:
            if spec.refresh is None:
                continue
            try:
                spec.refresh()
            except Exception as exc:
                self._show_error(f"{spec.title} 새로 고침 실패", exc)
        self._domain_key = ""
        self._navigation_history.clear()
        self._update_navigation_buttons()
        self._offset = 0
        self._require(self._stack).setCurrentIndex(0)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._search_timer.stop()
        errors: list[Exception] = []
        try:
            self._controller.close()
        except Exception as exc:
            errors.append(exc)
        for spec in self._domain_page_specs:
            try:
                spec.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(
                f"게임 자료실을 닫는 중 구성 요소 {len(errors)}개가 실패했습니다"
            ) from errors[0]

    def _build_browser(self, parent: QWidget) -> QWidget:
        host = QWidget(parent)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        bar = QFrame(host)
        bar.setObjectName("staticCatalogBrowserBar")
        bar.setStyleSheet(themed_style(
            "QFrame#staticCatalogBrowserBar{background:transparent;"
            "border:0;border-bottom:1px solid #21262d;}"
        ))
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 5)
        back = self._navigation_button(bar)
        self._domain_title = QLabel("", bar)
        self._domain_title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        bar_layout.addWidget(back)
        bar_layout.addWidget(self._domain_title)
        bar_layout.addStretch(1)
        layout.addWidget(bar)

        splitter = QSplitter(Qt.Horizontal, host)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_result_pane(splitter))
        splitter.addWidget(self._build_detail_pane(splitter))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([390, 760])
        layout.addWidget(splitter, 1)
        return host

    def _build_domain_page(
        self,
        spec: StaticCatalogDomainPageSpec,
        parent: QWidget,
    ) -> tuple[QWidget, QWidget]:
        host = QWidget(parent)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        bar = QFrame(host)
        bar.setObjectName("staticCatalogDomainPageBar")
        bar.setStyleSheet(themed_style(
            "QFrame#staticCatalogDomainPageBar{background:transparent;"
            "border:0;border-bottom:1px solid #21262d;}"
        ))
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 5)
        back = self._navigation_button(bar)
        title = QLabel(spec.title, bar)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        bar_layout.addWidget(back)
        bar_layout.addWidget(title)
        bar_layout.addStretch(1)
        layout.addWidget(bar)
        try:
            content = spec.build(host)
        except Exception:
            host.deleteLater()
            raise
        layout.addWidget(content, 1)
        return host, content

    def _build_result_pane(self, parent: QWidget) -> QWidget:
        pane = QFrame(parent)
        pane.setObjectName("staticCatalogResultPane")
        pane.setStyleSheet(themed_style(
            "QFrame#staticCatalogResultPane{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        ))
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 12, 10, 10)
        self._search_edit = QLineEdit(pane)
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setPlaceholderText("이름(한글/중국어), 정식 ID, GA, GE, Buff key, Gameplay Tag, 리소스 경로 검색")
        self._search_edit.textChanged.connect(lambda _text: self._queue_search())
        layout.addWidget(self._search_edit)
        self._result_list = QListWidget(pane)
        self._result_list.setObjectName("staticCatalogResults")
        self._result_list.currentItemChanged.connect(self._on_result_changed)
        layout.addWidget(self._result_list, 1)
        pager = QHBoxLayout()
        self._previous_button = QPushButton("이전 페이지", pane)
        self._next_button = QPushButton("다음 페이지", pane)
        self._page_label = QLabel("0개", pane)
        self._page_label.setAlignment(Qt.AlignCenter)
        self._page_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        self._previous_button.clicked.connect(lambda: self._change_page(-1))
        self._next_button.clicked.connect(lambda: self._change_page(1))
        pager.addWidget(self._previous_button)
        pager.addWidget(self._page_label, 1)
        pager.addWidget(self._next_button)
        layout.addLayout(pager)
        return pane

    def _build_detail_pane(self, parent: QWidget) -> QWidget:
        pane = QFrame(parent)
        pane.setObjectName("staticCatalogDetailPane")
        pane.setStyleSheet(themed_style(
            "QFrame#staticCatalogDetailPane{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        ))
        outer = QVBoxLayout(pane)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(pane)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(scroll)
        self._detail_layout = QVBoxLayout(host)
        self._detail_layout.setContentsMargins(16, 14, 16, 16)
        self._detail_layout.setSpacing(10)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        self._render_empty_detail("자료를 선택하면 정식 필드, 관계, 출처 표시를 볼 수 있습니다.")
        return pane

    def _queue_search(self) -> None:
        self._offset = 0
        self._search_timer.start()

    def _open_domain(self, domain_key: str) -> None:
        if domain_key not in self._domain_labels:
            return
        self._domain_key = domain_key
        self._offset = 0
        search = self._require(self._search_edit)
        search.blockSignals(True)
        search.clear()
        search.blockSignals(False)
        self._require(self._domain_title).setText(self._domain_labels[domain_key])
        spec = self._domain_page_specs_by_key.get(domain_key)
        if spec is not None:
            dedicated_page = self._domain_pages.get(domain_key)
            if dedicated_page is None:
                stack = self._require(self._stack)
                try:
                    dedicated_page, content = self._build_domain_page(spec, stack)
                except Exception as exc:
                    self._show_error(f"{spec.title}을(를) 열 수 없음", exc)
                    self._show_menu()
                    return
                self._domain_pages[domain_key] = dedicated_page
                self._domain_contents[domain_key] = content
                if isinstance(content, CatalogDomainNavigation):
                    content.set_catalog_navigation_listener(
                        self._update_navigation_buttons
                    )
                stack.addWidget(dedicated_page)
            self._require(self._stack).setCurrentWidget(dedicated_page)
            self._update_navigation_buttons()
            return
        self._require(self._stack).setCurrentIndex(1)
        self._run_search()

    def open_catalog_link(self, link: "CatalogLink") -> bool:
        """Open one deliberate detail link with reversible cross-domain history.

        Ordinary relations are rendered by the owning domain page and never
        arrive here.  A page calls this method only for an explicit
        ``查看详情`` action.  When the target belongs to another domain, the
        live origin widget is retained so going back restores its exact filter,
        selection, scroll and nested-detail state.
        """

        domain_key = str(link.domain_key)
        origin_key = self._current_domain_key()
        origin_widget = self._require(self._stack).currentWidget()
        history_size = len(self._navigation_history)
        if origin_key and origin_key != domain_key:
            self._navigation_history.append(CatalogNavigationEntry(
                origin_key,
                self._domain_labels.get(origin_key, origin_key),
            ))
        try:
            self._open_domain(domain_key)
            content = self._domain_contents.get(domain_key)
            if content is None:
                raise LookupError("이 자료 영역은 현재 사용할 수 없습니다")
            record_id = str(link.record_id)
            if domain_key == "character":
                try:
                    character_id = int(record_id)
                except ValueError as exc:
                    raise LookupError("캐릭터 자료를 더 이상 사용할 수 없습니다") from exc
                opener = getattr(content, "open_character", None)
                argument: object = character_id
            elif domain_key == "fork":
                opener = getattr(content, "open_fork", None)
                argument = record_id
            elif domain_key == "equipment":
                method_name = {
                    "suit": "open_suit",
                    "shape": "open_shape",
                }.get(str(link.relation_kind), "open_equipment")
                opener = getattr(content, method_name, None)
                argument = record_id
            else:
                opener = getattr(content, "open_record", None)
                argument = record_id
            if not callable(opener):
                raise LookupError("이 자료에는 열 수 있는 상세 정보가 없습니다")
            if opener(argument) is False:
                raise LookupError("이 연관 자료는 더 이상 유효하지 않습니다")
        except Exception:
            del self._navigation_history[history_size:]
            self._require(self._stack).setCurrentWidget(origin_widget)
            self._domain_key = origin_key
            self._update_navigation_buttons()
            QMessageBox.warning(
                self._dialog_parent,
                "연관 자료를 열 수 없음",
                "이 연관 자료는 현재 사용할 수 없어 원래 페이지로 돌아갔습니다.",
            )
            return False
        self._update_navigation_buttons()
        return True

    def _show_menu(self) -> None:
        self._search_timer.stop()
        self._domain_key = ""
        self._navigation_history.clear()
        self._require(self._stack).setCurrentIndex(0)
        self._update_navigation_buttons()

    def _navigation_button(self, parent: QWidget) -> QPushButton:
        button = QPushButton("‹ 자료실", parent)
        button.setObjectName("staticCatalogNavigateBack")
        button.setMinimumHeight(28)
        button.clicked.connect(self._go_back)
        self._navigation_buttons.append(button)
        return button

    def _go_back(self) -> None:
        if self._navigation_history:
            entry = self._navigation_history.pop()
            target = self._domain_pages.get(entry.domain_key)
            if target is None:
                if entry.domain_key not in self._domain_labels:
                    self._show_menu()
                    return
                self._domain_key = entry.domain_key
                self._require(self._stack).setCurrentIndex(1)
                self._update_navigation_buttons()
                return
            self._domain_key = entry.domain_key
            self._require(self._stack).setCurrentWidget(target)
            self._update_navigation_buttons()
            return
        content = self._domain_contents.get(self._domain_key)
        if (
            isinstance(content, CatalogDomainNavigation)
            and content.catalog_back_label() is not None
            and content.catalog_go_back()
        ):
            self._update_navigation_buttons()
            return
        self._show_menu()

    def _local_back_label(self) -> str | None:
        content = self._domain_contents.get(self._domain_key)
        if isinstance(content, CatalogDomainNavigation):
            return content.catalog_back_label()
        return None

    def _current_domain_key(self) -> str:
        stack = self._require(self._stack)
        if stack.currentIndex() == 0:
            return ""
        return self._domain_key

    def _update_navigation_buttons(self) -> None:
        if self._navigation_history:
            previous = self._navigation_history[-1]
            text = f"‹ {previous.title}(으)로 돌아가기"
            tooltip = "이전 페이지로 돌아가며 필터, 선택, 스크롤 위치를 유지합니다"
        elif local_label := self._local_back_label():
            text = f"‹ {local_label}(으)로 돌아가기"
            tooltip = f"{local_label}(으)로 돌아가기"
        else:
            text = "‹ 자료실"
            tooltip = "게임 자료실로 돌아가기"
        for button in self._navigation_buttons:
            button.setText(text)
            button.setToolTip(tooltip)

    def _run_search(self) -> None:
        if self._page is None or not self._domain_key:
            return
        query = self._require(self._search_edit).text()
        try:
            page = self._controller.search(
                domain_key=self._domain_key,
                query=query,
                offset=self._offset,
            )
        except Exception as exc:
            self._show_error("게임 자료실 조회 실패", exc)
            return
        self._total = page.total
        results = self._require(self._result_list)
        results.blockSignals(True)
        results.clear()
        for catalog_item in page.items:
            item = QListWidgetItem(catalog_item.title)
            item.setData(Qt.UserRole, catalog_item)
            item.setToolTip(catalog_item.subtitle)
            results.addItem(item)
        results.blockSignals(False)
        if results.count():
            results.setCurrentRow(0)
        else:
            self._render_empty_detail("일치하는 자료가 없습니다.")
        start = page.offset + 1 if page.items else 0
        end = page.offset + len(page.items)
        self._require(self._page_label).setText(f"{start}–{end} / {page.total}")
        self._require(self._previous_button).setEnabled(page.offset > 0)
        self._require(self._next_button).setEnabled(end < page.total)

    def _change_page(self, direction: int) -> None:
        step = self._controller.PAGE_SIZE
        self._offset = max(0, self._offset + direction * step)
        self._run_search()

    def _on_result_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        item = current.data(Qt.UserRole)
        if not isinstance(item, CatalogItem):
            return
        self._load_detail(item.domain_key, item.record_id)

    def _load_detail(self, domain_key: str, record_id: str) -> None:
        try:
            detail = self._controller.detail(domain_key=domain_key, record_id=record_id)
        except Exception as exc:
            self._show_error("자료 상세 정보 읽기 실패", exc)
            return
        if detail is None:
            self._render_empty_detail("이 기록은 더 이상 존재하지 않거나 현재 dataset에 속하지 않습니다.")
            return
        self._render_detail(detail)

    def _render_detail(self, detail: CatalogDetail) -> None:
        layout = self._clear_detail()
        title = QLabel(detail.item.title)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style("font-size:18px;font-weight:800;color:#f0f6fc"))
        layout.addWidget(title)
        if detail.item.subtitle:
            subtitle = QLabel(detail.item.subtitle)
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet(themed_style("color:#8b949e"))
            layout.addWidget(subtitle)
        for section in detail.sections:
            heading = QLabel(section.title)
            heading.setStyleSheet(themed_style(
                "font-size:14px;font-weight:800;color:#58a6ff;margin-top:6px"
            ))
            layout.addWidget(heading)
            for field in section.fields:
                layout.addWidget(self._field_widget(field))
            if section.references:
                references = QHBoxLayout()
                references.setSpacing(6)
                for reference in section.references:
                    button = QPushButton(reference.label)
                    button.setObjectName("btnSm")
                    button.clicked.connect(partial(
                        self._load_detail,
                        reference.domain_key,
                        reference.record_id,
                    ))
                    references.addWidget(button)
                references.addStretch(1)
                layout.addLayout(references)
        for group in detail.relation_groups:
            layout.addWidget(self._relation_group_widget(detail, group))
        for note in detail.notes:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#d29922;background:#d2992222;border:1px solid #d2992266;"
                "border-radius:6px;padding:7px"
            ))
            layout.addWidget(label)
        layout.addStretch(1)

    def _relation_group_widget(
        self,
        detail: CatalogDetail,
        group: CatalogRelationGroup,
    ) -> QWidget:
        host = QFrame()
        host.setObjectName("staticCatalogRelationGroup")
        host.setStyleSheet(themed_style(
            "QFrame#staticCatalogRelationGroup{border:1px solid #30363d;"
            "border-radius:7px;padding:5px}"
        ))
        layout = QVBoxLayout(host)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(7)
        heading = QLabel(f"{group.label} ({group.total})", host)
        heading.setStyleSheet(themed_style("font-weight:800;color:#58a6ff"))
        layout.addWidget(heading)
        rows_layout = QVBoxLayout()
        rows_layout.setSpacing(7)
        layout.addLayout(rows_layout)
        button = QPushButton(f"{group.label} 불러오기", host)
        button.setObjectName("staticCatalogLoadRelations")
        layout.addWidget(button)
        state = {"offset": 0}

        def load_page() -> None:
            try:
                page = self._controller.relations(
                    domain_key=detail.item.domain_key,
                    record_id=detail.item.record_id,
                    relation_kind=group.kind,
                    offset=state["offset"],
                )
            except Exception as exc:
                button.setText(f"불러오기 실패: {exc}")
                button.setEnabled(False)
                return
            for row in page.rows:
                rows_layout.addWidget(self._relation_row_widget(row))
            state["offset"] = page.offset + len(page.rows)
            has_more = state["offset"] < page.total
            button.setEnabled(has_more)
            button.setText(
                f"다음 페이지 불러오기 ({state['offset']} / {page.total})"
                if has_more else f"모두 불러옴 ({page.total})"
            )

        button.clicked.connect(load_page)
        return host

    def _relation_row_widget(self, section: CatalogSection) -> QWidget:
        host = QFrame()
        host.setObjectName("staticCatalogRelationRow")
        host.setStyleSheet(themed_style(
            "QFrame#staticCatalogRelationRow{background:#0d1117;"
            "border:1px solid #21262d;border-radius:6px;padding:4px}"
        ))
        layout = QVBoxLayout(host)
        layout.setContentsMargins(7, 5, 7, 6)
        title = QLabel(section.title, host)
        title.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
        layout.addWidget(title)
        for field in section.fields:
            layout.addWidget(self._field_widget(field))
        if section.references:
            links = QHBoxLayout()
            for reference in section.references:
                button = QPushButton(reference.label, host)
                button.setObjectName("staticCatalogRelationTarget")
                button.clicked.connect(partial(
                    self._load_detail,
                    reference.domain_key,
                    reference.record_id,
                ))
                links.addWidget(button)
            links.addStretch(1)
            layout.addLayout(links)
        return host

    def _field_widget(self, field: CatalogField) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        name = QLabel(field.label)
        name.setMinimumWidth(96)
        name.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        value = QLabel(field.value or "—")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value.setStyleSheet(themed_style("color:#c9d1d9"))
        source = QLabel(SOURCE_LABELS[field.source])
        source.setStyleSheet(themed_style(
            "color:#58a6ff;background:#1f6feb22;border:1px solid #1f6feb66;"
            "border-radius:5px;padding:2px 5px;font-size:10px"
        ))
        layout.addWidget(name)
        layout.addWidget(value, 1)
        layout.addWidget(source, 0, Qt.AlignTop)
        if field.copyable:
            copy_button = QPushButton("복사")
            copy_button.setObjectName("btnSm")
            copy_button.clicked.connect(
                lambda _checked=False, text=field.value: QApplication.clipboard().setText(text)
            )
            layout.addWidget(copy_button, 0, Qt.AlignTop)
        return row

    def _render_empty_detail(self, message: str) -> None:
        layout = self._clear_detail()
        label = QLabel(message)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(themed_style("color:#8b949e;padding:24px"))
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)

    def _clear_detail(self) -> QVBoxLayout:
        layout = self._require(self._detail_layout)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                self._delete_layout(child_layout)
        return layout

    def _delete_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                self._delete_layout(child)
        layout.deleteLater()

    def _show_error(self, title: str, error: Exception) -> None:
        self._render_empty_detail(f"{title}: {error}")
        QMessageBox.warning(self._dialog_parent, title, str(error))

    @staticmethod
    def _require(value):
        if value is None:
            raise RuntimeError("게임 자료실 페이지가 아직 구성되지 않았습니다")
        return value
