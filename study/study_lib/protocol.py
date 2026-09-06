"""GEN-PROTOCOL 실행 계층 — 100% JSON 페이로드 검증/예산.

계약(스키마)은 `config/gen_protocol/v1/schema.json` 을 단일 원천으로 읽는다.
- `allowed_keys()`: 과목(kind 슬롯 + 주제 팩)에서 허용되는 키 집합.
- `validate()`: 페이로드를 스키마·필드 예산·빈 슬롯 기준으로 검사.
  → `ValidationReport.ok / issues / patch_slots` (다시 채워야 할 슬롯).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .tokens import Tokenize, default_tokenize

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "gen_protocol" / "v1" / "schema.json"
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    budget: int
    allowed: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SlotSpec:
    key: str
    type: str  # "string" | "array"
    max: int | None = None
    budget: int | None = None
    fields: tuple[FieldSpec, ...] = ()


@dataclass(frozen=True)
class KindSpec:
    kind: str
    slots: dict[str, SlotSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class Schema:
    version: str
    subject_keys: dict[str, tuple[str, ...]]
    kinds: dict[str, KindSpec]


def load_schema(path: str | Path = DEFAULT_SCHEMA_PATH) -> Schema:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    subject_keys = {s: tuple(ks) for s, ks in data["keys"]["subjects"].items()}
    kinds: dict[str, KindSpec] = {}
    for kind, slots in data["kinds"].items():
        specs: dict[str, SlotSpec] = {}
        for key, spec in slots.items():
            fields = tuple(
                FieldSpec(
                    key=fk,
                    budget=int(f.get("budget", 0)),
                    allowed=tuple(f.get("allowed", [])) or None,
                )
                for fk, f in spec.get("fields", {}).items()
            )
            specs[key] = SlotSpec(
                key=key,
                type=spec["type"],
                max=spec.get("max"),
                budget=spec.get("budget"),
                fields=fields,
            )
        kinds[kind] = KindSpec(kind=kind, slots=specs)
    return Schema(version=data["version"], subject_keys=subject_keys, kinds=kinds)


@dataclass
class ValidationReport:
    ok: bool
    issues: list[str]
    patch_slots: set[str]


def allowed_keys(schema: Schema, subject: str, kind: str) -> set[str]:
    keys = set(schema.kinds[kind].slots) if kind in schema.kinds else set()
    keys |= set(schema.subject_keys.get(subject, ()))
    return keys


def _len(tok: Tokenize, text: str) -> int:
    return tok(text)


def _check_slot(slot: SlotSpec, key: str, value: object, tok: Tokenize,
                issues: list[str], patch: set[str]) -> None:
    if slot.type == "string":
        if not isinstance(value, str):
            issues.append(f"{key}: expected a string, got {type(value).__name__}")
            return
        if not value.strip():
            issues.append(f"{key}: empty content")
            patch.add(key)
        elif slot.budget is not None and _len(tok, value) > slot.budget:
            issues.append(f"{key}: exceeds budget {slot.budget} tokens (got {_len(tok, value)})")
            patch.add(key)
        return

    if not isinstance(value, list):
        issues.append(f"{key}: expected an array, got {type(value).__name__}")
        return
    if not value:
        issues.append(f"{key}: empty array (omit the key instead of sending empty)")
        patch.add(key)
        return
    if slot.max is not None and len(value) > slot.max:
        issues.append(f"{key}: too many items ({len(value)} > {slot.max})")

    if slot.fields:
        field_keys = {f.key for f in slot.fields}
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                issues.append(f"{key}[{i}]: expected an object, got {type(item).__name__}")
                continue
            for unknown in sorted(set(item) - field_keys):
                issues.append(f"{key}[{i}]: unknown field {unknown!r}")
            has_content = False
            for f in slot.fields:
                if f.key not in item:
                    continue
                v = item[f.key]
                if not isinstance(v, str):
                    issues.append(f"{key}[{i}].{f.key}: expected a string")
                    continue
                if not v.strip():
                    issues.append(f"{key}[{i}].{f.key}: empty")
                    continue
                has_content = True
                if f.budget and _len(tok, v) > f.budget:
                    issues.append(f"{key}[{i}].{f.key}: exceeds budget {f.budget} tokens "
                                  f"(got {_len(tok, v)})")
                    patch.add(key)  # 예산 초과 슬롯은 재요청 대상
                if f.allowed and v not in f.allowed:
                    issues.append(f"{key}[{i}].{f.key}: value {v!r} not allowed "
                                  f"{sorted(f.allowed)}")
            if not has_content:
                issues.append(f"{key}[{i}]: no content")
                patch.add(key)
    else:
        for i, item in enumerate(value):
            if not isinstance(item, str):
                issues.append(f"{key}[{i}]: expected a string, got {type(item).__name__}")
            elif not item.strip():
                issues.append(f"{key}[{i}]: empty item")
                patch.add(key)
            elif slot.budget is not None and _len(tok, item) > slot.budget:
                issues.append(f"{key}[{i}]: exceeds budget {slot.budget} tokens "
                              f"(got {_len(tok, item)})")


def _check_extension(key: str, value: object, issues: list[str], patch: set[str]) -> None:
    """주제 팩 키 — 최상위 스칼라 문자열 또는 문자열 배열 (비어 있으면 안 됨)."""
    if isinstance(value, str):
        if not value.strip():
            issues.append(f"{key}: empty content")
            patch.add(key)
    elif isinstance(value, list):
        if not value:
            issues.append(f"{key}: empty array")
            patch.add(key)
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                issues.append(f"{key}[{i}]: expected a non-empty string")
                patch.add(key)
    else:
        issues.append(f"{key}: expected a string or array of strings, got {type(value).__name__}")


def validate(schema: Schema, payload: object, subject: str, kind: str,
             tokenize: Tokenize | None = None) -> ValidationReport:
    """클라이언트가 쓸 검증 — 페이로드가 과목(kind) 스키마·예산에 맞는지."""
    tok = tokenize or default_tokenize
    if not isinstance(payload, dict):
        return ValidationReport(
            ok=False,
            issues=[f"payload must be a JSON object, got {type(payload).__name__}"],
            patch_slots=set(),
        )
    if not payload:
        return ValidationReport(ok=False, issues=["payload is empty"], patch_slots=set())
    if kind not in schema.kinds:
        return ValidationReport(
            ok=False,
            issues=[f"unknown kind {kind!r}; known: {sorted(schema.kinds)}"],
            patch_slots=set(),
        )

    allowed = allowed_keys(schema, subject, kind)
    issues: list[str] = []
    patch: set[str] = set()
    for key, value in payload.items():
        if key not in allowed:
            issues.append(f"unknown key {key!r} for subject={subject!r}, kind={kind!r}")
            continue
        slot = schema.kinds[kind].slots.get(key)
        if slot is not None:
            _check_slot(slot, key, value, tok, issues, patch)
        else:
            _check_extension(key, value, issues, patch)
    return ValidationReport(ok=not issues, issues=issues, patch_slots=patch)
