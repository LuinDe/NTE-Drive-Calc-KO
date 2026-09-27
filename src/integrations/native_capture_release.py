# 核对随插件工作区交付的独立采集组件及协议清单。
from __future__ import annotations

import hashlib
import json
from pathlib import Path


NATIVE_CAPTURE_FILES = (
    Path("NTE_Capture.dll"), Path("native-capture.json"),
    Path("NTE_Capture.LICENSE.txt"), Path("NTE_Capture.NOTICES.txt"),
)


def native_capture_capabilities(workspace: Path) -> frozenset[str]:
    """Read independently declared business capabilities after file/hash validation."""
    if not all((workspace / item).is_file() for item in NATIVE_CAPTURE_FILES):
        return frozenset()
    try:
        manifest = json.loads((workspace / "native-capture.json").read_text(encoding="utf-8"))
        with (workspace / "NTE_Capture.dll").open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        capabilities = manifest.get("capabilities")
        if (manifest.get("protocol_version") != 1 or manifest.get("sha256", "").lower() != digest
                or not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities)):
            return frozenset()
        return frozenset(capabilities)
    except (OSError, ValueError, TypeError, AttributeError):
        return frozenset()


def validate_native_capture_release(workspace: Path) -> None:
    if not all((workspace / item).is_file() for item in NATIVE_CAPTURE_FILES):
        raise ValueError("강화 수집 컴포넌트가 불완전합니다. 함께 제공되는 설치 패키지를 복구해 주세요.")
    try:
        manifest = json.loads((workspace / "native-capture.json").read_text(encoding="utf-8"))
        with (workspace / "NTE_Capture.dll").open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        valid = (
            manifest["protocol_version"] == 1
            and "combat.hit_buff.v1" in manifest["capabilities"]
            and "combat.context.v1" in manifest["capabilities"]
            and str(manifest["sha256"]).lower() == digest
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ValueError("강화 수집 컴포넌트 매니페스트가 유효하지 않습니다. 함께 제공되는 설치 패키지를 복구해 주세요.") from error
    if not valid:
        raise ValueError("강화 수집 컴포넌트의 버전 또는 검증 값이 일치하지 않습니다. 함께 제공되는 설치 패키지를 복구해 주세요.")
