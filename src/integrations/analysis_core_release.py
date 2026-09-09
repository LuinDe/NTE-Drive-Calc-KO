# 定位和核对独立 Rust 分析组件，保持采集 Core 路径不变。
"""Resolve the optional independently packaged analysis executable."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path

from src.integrations.bundled_resources import bundled_root
from src.integrations.nte_analysis_core import (
    SUPPORTED_ENGINE_VERSIONS, NativeAnalysisError, NteAnalysisCoreClient,
)


def create_bundled_analysis_client(
    *, static_database_path: Path | None,
    cancelled: Callable[[], bool] | None = None,
) -> NteAnalysisCoreClient | None:
    """Bind once to the packaged engine; a broken installation is an error."""
    if static_database_path is None:
        return None
    root = bundled_root()
    locations = (
        (root / "nte-analysis-core.exe", root / "analysis-core-meta/component.json"),
        (root / "third_party/analysis-core/bin/nte-analysis-core.exe",
         root / "third_party/analysis-core/component.json"),
    )
    for executable, manifest_path in locations:
        if not executable.exists() and not manifest_path.exists():
            continue
        if not executable.is_file() or not manifest_path.is_file():
            raise NativeAnalysisError("독립 분석 구성 요소가 불완전합니다. 다시 배포하세요")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest["engine"] != "nte-analysis-core"
                or manifest["engine_version"] not in SUPPORTED_ENGINE_VERSIONS
                or manifest["sha256"] != hashlib.sha256(executable.read_bytes()).hexdigest()
            ):
                raise NativeAnalysisError("독립 분석 구성 요소의 버전 또는 해시가 일치하지 않습니다")
            capabilities = manifest.get("capabilities", [])
            if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
                raise NativeAnalysisError("독립 분석 구성 요소의 기능 목록이 유효하지 않습니다")
            static_manifest = json.loads(
                (Path(static_database_path).parent / "manifest.json").read_text(encoding="utf-8")
            )
            dataset_version = str(static_manifest["database"]["dataset_id"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise NativeAnalysisError("독립 분석 구성 요소 또는 정적 데이터셋 매니페스트가 유효하지 않습니다") from error
        return NteAnalysisCoreClient(
            executable, dataset_version=dataset_version, cancelled=cancelled,
            engine_version=manifest["engine_version"],
            capabilities=frozenset(capabilities),
        )
    return None
