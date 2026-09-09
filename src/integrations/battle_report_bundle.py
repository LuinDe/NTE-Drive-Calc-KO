# 读写带版本的压缩战报 JSON 二进制容器。

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any, Mapping


BATTLE_REPORT_BUNDLE_EXTENSION = ".ntebr"
BATTLE_REPORT_BUNDLE_MAGIC = b"NTEBR\x1a\r\n"
BATTLE_REPORT_BUNDLE_VERSION = 1
_COMPRESSION_ZLIB = 1
_HEADER = struct.Struct(">8sBBHQQ32s")
_MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_JSON_BYTES = 1024 * 1024 * 1024


class BattleReportBundleError(ValueError):
    """The selected file is not a supported, intact battle-report package."""


def encode_battle_report_bundle(payload: Mapping[str, Any]) -> bytes:
    try:
        raw = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BattleReportBundleError("전투 리포트 번들 내용을 직렬화할 수 없습니다") from error
    if len(raw) > _MAX_JSON_BYTES:
        raise BattleReportBundleError("전투 리포트 번들의 압축 해제 후 내용이 너무 큽니다")
    compressed = zlib.compress(raw, level=9)
    digest = hashlib.sha256(raw).digest()
    return _HEADER.pack(
        BATTLE_REPORT_BUNDLE_MAGIC,
        BATTLE_REPORT_BUNDLE_VERSION,
        _COMPRESSION_ZLIB,
        0,
        len(raw),
        len(compressed),
        digest,
    ) + compressed


def decode_battle_report_bundle(data: bytes) -> dict[str, Any]:
    if len(data) < _HEADER.size:
        raise BattleReportBundleError("파일이 완전한 NTE 전투 리포트 번들이 아닙니다")
    magic, version, compression, flags, raw_size, packed_size, digest = (
        _HEADER.unpack(data[: _HEADER.size])
    )
    if magic != BATTLE_REPORT_BUNDLE_MAGIC:
        raise BattleReportBundleError("파일이 NTE 전투 리포트 번들이 아닙니다")
    if version != BATTLE_REPORT_BUNDLE_VERSION:
        raise BattleReportBundleError(f"지원하지 않는 전투 리포트 번들 컨테이너 버전: {version}")
    if compression != _COMPRESSION_ZLIB or flags != 0:
        raise BattleReportBundleError("전투 리포트 번들이 현재 버전에서 지원하지 않는 인코딩을 사용합니다")
    if packed_size > _MAX_COMPRESSED_BYTES or raw_size > _MAX_JSON_BYTES:
        raise BattleReportBundleError("전투 리포트 번들 내용이 너무 큽니다")
    compressed = data[_HEADER.size :]
    if len(compressed) != packed_size:
        raise BattleReportBundleError("전투 리포트 번들 압축 길이 검증 실패")
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(compressed, _MAX_JSON_BYTES + 1)
        if inflater.unconsumed_tail:
            raise BattleReportBundleError("전투 리포트 번들의 압축 해제 후 내용이 너무 큽니다")
        raw += inflater.flush()
    except zlib.error as error:
        raise BattleReportBundleError("전투 리포트 번들 압축 해제 실패") from error
    if not inflater.eof:
        raise BattleReportBundleError("전투 리포트 번들 압축 데이터가 불완전합니다")
    if inflater.unused_data:
        raise BattleReportBundleError("전투 리포트 번들에 잘못된 추가 압축 데이터가 있습니다")
    if len(raw) != raw_size or len(raw) > _MAX_JSON_BYTES:
        raise BattleReportBundleError("전투 리포트 번들 압축 해제 길이 검증 실패")
    if hashlib.sha256(raw).digest() != digest:
        raise BattleReportBundleError("전투 리포트 번들 SHA-256 검증 실패")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BattleReportBundleError("전투 리포트 번들 내부가 유효한 UTF-8 JSON이 아닙니다") from error
    if not isinstance(payload, dict):
        raise BattleReportBundleError("전투 리포트 번들 최상위는 객체여야 합니다")
    return payload


def read_battle_report_bundle(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        size = source.stat().st_size
        if size > _MAX_COMPRESSED_BYTES + _HEADER.size:
            raise BattleReportBundleError("전투 리포트 번들 파일이 너무 큽니다")
        return decode_battle_report_bundle(source.read_bytes())
    except OSError as error:
        raise BattleReportBundleError("전투 리포트 번들을 읽을 수 없습니다") from error


def write_battle_report_bundle_atomic(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    before_replace,
) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = encode_battle_report_bundle(payload)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        before_replace()
        os.replace(temporary_path, target)
        temporary_path = None
        return len(encoded)
    except OSError as error:
        raise BattleReportBundleError("전투 리포트 번들을 원자적으로 기록할 수 없습니다") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
