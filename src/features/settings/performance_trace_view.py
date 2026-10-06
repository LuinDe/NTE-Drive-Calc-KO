# 展示手动细分性能采样、服务选择与最近窗口的阶段耗时。
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                               QPushButton, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView)
from PySide6.QtCore import Qt

from src.integrations.performance_trace import SERVICES

LABELS = ('HUD 매 프레임', 'HUD 이벤트', 'HUD 수치 이벤트', '동기화 폴링', '스냅샷 읽기', '전투 시계', '전투 관측')
PHASE_LABELS = {'original': '게임 원본 콜백(Calc 외)', 'gate': '진입 검사', 'inbox': '이벤트 병합',
                'discovery': '객체 탐색', 'setup': '렌더링 준비', 'boss': '적 상태', 'team': '팀 HUD',
                'nameplate': '이름표', 'infoLog': '진단 로그', 'draw': '자체 렌더링 합계',
                'body': '전체 콜백(원본 콜백 포함)', 'outside': '콜백 외 간격', 'gap': '인접 콜백 간격',
                'teamCooldowns': '재사용 대기시간 조회', 'teamTextures': '아이콘 리소스', 'teamMirror': '네이티브 컨트롤 갱신',
                'teamCanvas': '팀 Canvas 렌더링'}


class PerformanceTraceView(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        note = QLabel('세부 샘플링 · 실행 중인 기능의 단계별 소요 시간을 기록합니다. 최대 120초 / 64 MiB. '
                      '결과는 서비스별 최근 최대 600회 콜백 묶음이며, 중첩된 단계는 합산할 수 없고 게임 프레임률도 아닙니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        self.choices = []
        for i, label in enumerate(LABELS):
            box = QCheckBox(label)
            box.setToolTip(SERVICES[i])
            box.setChecked(i == 0)
            grid.addWidget(box, i // 4, i % 4)
            self.choices.append(box)
        layout.addLayout(grid)
        row = QHBoxLayout()
        self.start = QPushButton('세부 샘플링 시작')
        self.stop = QPushButton('중지 후 저장')
        self.start.clicked.connect(self.begin)
        self.stop.clicked.connect(controller.stop_trace)
        row.addWidget(self.start)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = QLabel('아직 샘플링 안 함')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['서비스 / 단계', '샘플 수', 'P95 ms', 'P99 ms', '최대 ms'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

    def begin(self):
        try:
            self.controller.start_trace(sum(1 << i for i, box in enumerate(self.choices) if box.isChecked()))
        except (PermissionError, ValueError) as error:
            self.status.setText(str(error))

    def render(self, value):
        running = value.get('running', False)
        self.start.setEnabled(value.get('allowed', False) and not running)
        self.stop.setEnabled(running)
        for box in self.choices:
            box.setEnabled(not running)
        state = {'off': '아직 샘플링 안 함', 'starting': '켜는 중', 'collecting': '샘플링 중',
                 'saved': '중지 및 저장됨', 'failed': '샘플링이 완전히 끝나지 않음', 'unconfirmed': '중지 미확인'}.get(value.get('state'), '컴포넌트 대기 중')
        if running and value.get('stopping'):
            state = '중지하고 파일에 기록하는 중'
        text = f"{state} · 기록 {value.get('written', 0)}건 · 유실 {value.get('dropped', 0)}건"
        if value.get('error'):
            text += '\n' + value['error']
        if value.get('log_path'):
            text += '\nCalc 요약: ' + value['log_path']
        if value.get('path'):
            text += '\n원본 샘플: ' + value['path']
        self.status.setText(text)
        rows = []
        for summary in value.get('summaries', []):
            source = summary['source']
            for phase, result in summary['phases_us'].items():
                if result['n']:
                    label = PHASE_LABELS.get(phase, phase)
                    if phase == 'body' and source != 'hud_frame':
                        label = '서비스 호출 합계'
                    rows.append((f'{LABELS[SERVICES.index(source)]} / {label}', result))
        self.table.setRowCount(len(rows))
        for i, (name, result) in enumerate(rows):
            cells = [name, str(result['n']), *(f"{result[k]/1000:.3f}" for k in ('p95', 'p99', 'max'))]
            for j, cell in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(cell))
