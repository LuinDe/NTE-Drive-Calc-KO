# 从独立 PresentMon 子进程读取游戏呈现间隔，统计有界窗口并独立收尾。
from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import deque
from pathlib import Path
from threading import Lock, Thread
from time import monotonic

from src.integrations.presentmon_lifetime import (
    CollectorJob, reap_abandoned_sessions, session_name, stop_session,
)


class FrameWindow:
    """Present intervals; window by trace time, freshness by local arrival time."""
    def __init__(self):
        self.frames = deque(maxlen=60000)
        self.last_arrival = 0
        self.last_trace = -1
        self.overflow_until = -1

    def add(self, row, now):
        try:
            # PresentMon's legacy v1 CSV spells this with a lowercase m;
            # the unified v1-compatible serializer uses an uppercase M.
            milliseconds = float(row.get("msBetweenPresents", row.get("MsBetweenPresents")))
            timestamp = float(row["TimeInSeconds"])
            chain = row["SwapChainAddress"]
        except (KeyError, ValueError, TypeError):
            return
        if (not math.isfinite(milliseconds) or milliseconds <= 0
                or not math.isfinite(timestamp) or timestamp < 0 or not chain):
            return
        if self.frames and now - self.last_arrival > 3:
            self.frames.clear()
        self.last_arrival = now
        self.last_trace = max(timestamp, self.last_trace)
        if len(self.frames) == self.frames.maxlen:
            self.overflow_until = max(self.overflow_until, self.frames[0][0] + 30)
        self.frames.append((timestamp, milliseconds, chain))
        while self.frames and self.frames[0][0] < self.last_trace - 30:
            self.frames.popleft()

    def snapshot(self, now):
        empty = {"fps_milli": None, "frame_us": None, "low_milli": None}
        if not self.frames or now - self.last_arrival > 3:
            return empty
        counts = {}
        for time, _, chain in self.frames:
            if time >= self.last_trace - 1:
                counts[chain] = counts.get(chain, 0) + 1
        if not counts:
            return empty
        # Select the busiest present stream; never mix UI/video/game swap chains.
        chain = max(counts, key=counts.get)
        recent = [ms for time, ms, key in self.frames if key == chain and time >= self.last_trace - 1]
        window = [ms for time, ms, key in self.frames if key == chain and time >= self.last_trace - 30]
        mean = sum(recent) / len(recent)
        tail = sorted(window, reverse=True)[:math.ceil(len(window) * .01)]
        def wire(value):
            return round(value) if math.isfinite(value) and 1 <= value <= 1_000_000_000 else None
        return {"fps_milli": wire(1_000_000 / mean), "frame_us": wire(mean * 1000),
                "low_milli": wire(1_000_000 * len(tail) / sum(tail))
                if len(window) >= 1000 and self.last_trace > self.overflow_until else None}


class PresentMon:
    def __init__(self, executable: Path, pid_reader):
        self.executable, self.pid_reader = executable, pid_reader
        self._process = None
        self._thread = None
        self._stderr_thread = None
        self._stream_error = ""
        self._lock = Lock()
        self._window = FrameWindow()
        self._pid = None
        self._retry_at = 0
        self.error = ""
        self._session = ""
        self._verified = False
        self._job = None
        self._pid_probe_at = 0
        self._started_at = 0
        self._reaped = False

    def sample(self):
        now = monotonic()
        pid = self._pid
        if now >= self._pid_probe_at:
            self._pid_probe_at = now + 1
            try:
                pid = self.pid_reader()
            except Exception:
                self.close()
                return {'fps_milli': None, 'frame_us': None, 'low_milli': None,
                        'frame_error': '현재 게임 프로세스를 확인할 수 없습니다; 프레임 샘플링을 중지했습니다',
                        'frame_source': 'presentmon.present_interval', 'low_window_seconds': 30}
        if pid != self._pid:
            self.close()
            self._pid = pid
            self._retry_at = 0
        if not pid:
            self.error = "게임 대기 중"
        elif not self.executable.is_file():
            self.error = "동봉된 PresentMon이 없습니다; 프레임 레이트를 현재 사용할 수 없습니다"
        elif self._process is None or self._process.poll() is not None:
            if monotonic() >= self._retry_at:
                self.close()
                self._pid = pid
                self._retry_at = monotonic() + 10
                self._stream_error = ""
                try:
                    if not self._verified:
                        manifest = json.loads(self.executable.with_name("component.json").read_text(encoding="utf-8"))
                        if (manifest.get("component") != "presentmon-console" or manifest.get("version") != "2.5.1"
                                or hashlib.sha256(self.executable.read_bytes()).hexdigest() != manifest.get("sha256")):
                            raise ValueError("invalid_presentmon_component")
                        self._verified = True
                    if not self._reaped:
                        reap_abandoned_sessions()
                        self._reaped = True
                    self._session = session_name()
                    self._job = CollectorJob()
                    self._process = subprocess.Popen([
                        str(self.executable), "--process_id", str(pid), "--output_stdout",
                        "--v1_metrics", "--no_console_stats", "--no_track_input", "--no_track_gpu",
                        "--terminate_on_proc_exit", "--session_name", self._session,
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                       text=True, encoding="utf-8-sig", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self._job.attach(self._process)
                    self._started_at = monotonic()
                    self._thread = Thread(target=self._consume, args=(self._process, pid), daemon=True)
                    self._stderr_thread = Thread(target=self._consume_errors, args=(self._process,), daemon=True)
                    self.error = ""
                    self._thread.start()
                    self._stderr_thread.start()
                except (OSError, ValueError):
                    self.close()
                    self._pid = pid
                    self.error = "PresentMon 식별 확인 또는 시작 실패"
            else:
                self.error = "프레임 수집이 종료되었습니다; 재시도 대기 중 (수집 권한을 확인하세요)"
        with self._lock:
            values = self._window.snapshot(monotonic())
            error = self._stream_error or self.error
            if values["fps_milli"] is None and not error:
                error = ('프레임 샘플링 프로세스는 실행 중이지만 유효한 데이터가 계속 없습니다; ETW 샘플링 상태를 확인하세요'
                         if self._started_at and now - self._started_at >= 10 and not self._window.frames
                         else '게임 프레임 표시 대기 중; 아직 유효한 샘플이 없거나 샘플이 만료되었습니다')
            return {**values, "frame_error": error,
                    "frame_source": "presentmon.present_interval", "low_window_seconds": 30}

    def _consume(self, process, pid):
        try:
            reader = csv.DictReader(process.stdout)
            columns = set(reader.fieldnames or ())
            if not columns:
                return  # Startup failure is reported by stderr/exit, not a CSV schema error.
            if (not {"ProcessID", "TimeInSeconds", "SwapChainAddress"} <= columns
                    or not {"msBetweenPresents", "MsBetweenPresents"} & columns):
                with self._lock:
                    if process is self._process:
                        self._stream_error = "PresentMon 출력에 필요한 프레임 필드가 없습니다; 호환 컴포넌트를 확인하세요"
                return
            for row in reader:
                if row.get("ProcessID") != str(pid):
                    continue
                with self._lock:
                    if process is self._process:
                        self._window.add(row, monotonic())
        except (OSError, ValueError, csv.Error):
            with self._lock:
                if process is self._process:
                    self._stream_error = "PresentMon 프레임 데이터 읽기 실패"

    def _consume_errors(self, process):
        try:
            for line in process.stderr:
                lower = line.lower()
                message = ""
                if "access denied" in lower:
                    message = "프레임 수집 권한이 부족합니다; Calc를 관리자 권한으로 다시 연 후 게임 내 성능 표시를 켜세요"
                elif lower.startswith("error:"):
                    message = "PresentMon 시작 또는 수집 실패; 권한과 호환 컴포넌트를 확인하세요"
                elif 'etw' in lower and 'lost' in lower:
                    message = 'ETW 프레임 이벤트 손실; 현재 프레임 통계가 불완전할 수 있습니다'
                if message:
                    with self._lock:
                        if process is self._process:
                            self._stream_error = message
        except (OSError, ValueError):
            pass

    def close(self):
        process, self._process = self._process, None
        session, self._session = self._session, ''
        job, self._job = self._job, None
        self._started_at = 0
        # Stop ETW even if the collector has already died. No stop-helper process.
        if session:
            try:
                stop_session(session)
            except OSError:
                self.error = '프레임 샘플링 세션 중지가 확인되지 않았습니다; 다음 시작 시 소유자 없는 세션을 확인합니다'
        if process is not None:
            try:
                if process.poll() is None:
                    process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            finally:
                if job:
                    job.close()
                    job = None
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                self.error = '프레임 샘플링 프로세스 종료 미확인'
            # The first stop can race the child's StartTrace. After confirmed exit
            # it can no longer create a late session, so perform the final sweep.
            if session and process.poll() is not None:
                try:
                    stop_session(session)
                except OSError:
                    self.error = '프레임 샘플링 세션 중지가 확인되지 않았습니다; 다음 시작 시 소유자 없는 세션을 확인합니다'
            if self._thread:
                self._thread.join(timeout=1)
            if self._stderr_thread:
                self._stderr_thread.join(timeout=1)
            if process.stdout and not (self._thread and self._thread.is_alive()):
                process.stdout.close()
            if process.stderr and not (self._stderr_thread and self._stderr_thread.is_alive()):
                process.stderr.close()
        if job:
            job.close()
        with self._lock:
            self._window = FrameWindow()
        self._thread = None
        self._stderr_thread = None
        self._pid = None
