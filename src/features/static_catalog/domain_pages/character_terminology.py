# 角色图鉴对公共正式术语服务的 Qt 无关投影。
"""Character-page projection of shared localized catalog terms."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


@dataclass(frozen=True, slots=True)
class CharacterTermProjection:
    display_name: str
    more_info: tuple[tuple[str, str], ...]


def project_character_term(
    terminology: StaticCatalogTerminologyService | None,
    *,
    entity_kind: str,
    stable_id: str,
    identity_label: str,
    context: str | None = None,
) -> CharacterTermProjection:
    """Prefer a formal localized name and keep identity evidence collapsed."""

    requested_id = str(stable_id or "").strip()
    if not requested_id:
        return CharacterTermProjection("名称暂未提供", ())
    if terminology is None:
        return CharacterTermProjection(
            "名称暂未提供",
            ((f"{identity_label} 정식 ID", requested_id),),
        )
    term = terminology.resolve(
        entity_kind,
        requested_id,
        context=context,
    )
    rows = [
        (f"{identity_label} 요청 ID", term.requested_id),
    ]
    if term.canonical_id and term.canonical_id != term.requested_id:
        rows.append((f"{identity_label} 정식 ID", term.canonical_id))
    if term.text_table:
        rows.append((f"{identity_label} 텍스트 테이블", term.text_table))
    if term.text_key:
        rows.append((f"{identity_label} 텍스트 키", term.text_key))
    if term.resolved_locale:
        rows.append((f"{identity_label} 현지화", term.resolved_locale))
    return CharacterTermProjection(
        term.display_name or "名称暂未提供",
        tuple(rows),
    )
