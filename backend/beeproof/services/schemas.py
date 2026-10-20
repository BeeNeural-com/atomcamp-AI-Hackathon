"""Extraction schema registry. One built-in, immutable rate-sheet schema (PRODUCT_SPEC.md §9)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beeproof.contracts import FIELD_TYPES, FIELD_UNITS, FIELDS, ProductType
from beeproof.domain.validators import RULE_CATALOG, RULES_VERSION
from beeproof.orm import ExtractionSchema, ExtractionSchemaVersion
from beeproof.services.errors import AppError, not_found

RATE_SHEET = "rate_sheet"
DISPLAY_NAME = "Deposit rate sheet"
SUPPORTED_EXTRACTORS = {"rate_sheet_v1"}
ALLOWED_TYPES = {"string", "enum", "integer", "decimal", "date", "boolean"}
# rate_pct / apy_pct are individually optional but at least one is required (MISSING_REQUIRED rule)
REQUIRED = {"institution_name", "product_type", "term_months", "effective_date"}
DESCRIPTIONS = {
    "institution_name": "Name exactly as printed on the document.",
    "product_type": "Normalized deposit product category.",
    "term_months": "Certificate duration in months; 0 for non-term products.",
    "rate_pct": "Nominal rate in percentage points (4.25 means 4.25%).",
    "apy_pct": "Annual percentage yield in percentage points.",
    "min_deposit": "Minimum opening deposit in USD.",
    "effective_date": "Effective date in ISO YYYY-MM-DD format.",
}


def rate_sheet_schema_json() -> dict:
    fields = []
    for name in FIELDS:
        f = {
            "name": name,
            "type": FIELD_TYPES[name],
            "required": name in REQUIRED,
            "description": DESCRIPTIONS[name],
            "unit": FIELD_UNITS.get(name),
        }
        if name == "product_type":
            f["enum"] = [p.value for p in ProductType]
        fields.append(f)
    return {
        "name": RATE_SHEET,
        "title": DISPLAY_NAME,
        "extractor": "rate_sheet_v1",
        "record": "one row per deposit product",
        "fields": fields,
        "notes": "rate_pct or apy_pct must be present; term_months is 0 for non-term products",
    }


def version_wire(v: ExtractionSchemaVersion) -> dict:
    """SchemaVersion (lib/types.ts)."""
    sj = v.schema_json or {}
    return {
        "id": v.id,
        "name": sj.get("title") or v.schema.name,
        "version": v.version,
        "status": v.status,
        "fields": [
            {
                "name": f.get("name", ""),
                "type": f.get("type", "string"),
                "required": bool(f.get("required", False)),
                "description": f.get("description", ""),
            }
            for f in sj.get("fields", [])
        ],
        "schema_id": v.schema_id,
        "business_rules_json": v.business_rules_json,
    }


def published_versions(session: Session) -> list[ExtractionSchemaVersion]:
    return list(
        session.scalars(
            select(ExtractionSchemaVersion)
            .where(ExtractionSchemaVersion.status == "published")
            .order_by(ExtractionSchemaVersion.created_at)
        )
    )


def rate_sheet_rules_json() -> dict:
    return {"rules_version": RULES_VERSION, "rules": RULE_CATALOG}


def ensure_builtin_schema(session: Session) -> ExtractionSchemaVersion:
    schema = session.scalar(select(ExtractionSchema).where(ExtractionSchema.name == RATE_SHEET))
    if schema is None:
        schema = ExtractionSchema(name=RATE_SHEET, description="Bank / credit union deposit rate sheet rows")
        session.add(schema)
        session.flush()
    version = session.scalar(
        select(ExtractionSchemaVersion)
        .where(ExtractionSchemaVersion.schema_id == schema.id, ExtractionSchemaVersion.status == "published")
        .order_by(ExtractionSchemaVersion.version.desc())
    )
    if version is None:
        version = ExtractionSchemaVersion(
            schema_id=schema.id,
            version=1,
            schema_json=rate_sheet_schema_json(),
            business_rules_json=rate_sheet_rules_json(),
            status="published",
        )
        session.add(version)
        session.flush()
    return version


def validate_schema_json(schema_json: dict) -> list[dict]:
    """Structural validation; returns error details (empty = valid)."""
    if not isinstance(schema_json, dict):
        return [{"field": "schema_json", "reason": "must be an object"}]
    errs: list[dict] = []
    fields = schema_json.get("fields")
    if not isinstance(fields, list) or not fields:
        return [{"field": "schema_json.fields", "reason": "must be a non-empty list"}]
    seen = set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict) or not f.get("name"):
            errs.append({"field": f"schema_json.fields[{i}].name", "reason": "required"})
            continue
        if f["name"] in seen:
            errs.append({"field": f"schema_json.fields[{i}].name", "reason": "duplicate field name"})
        seen.add(f["name"])
        if f.get("type") not in ALLOWED_TYPES:
            errs.append(
                {
                    "field": f"schema_json.fields[{i}].type",
                    "reason": f"unsupported data type; allowed: {sorted(ALLOWED_TYPES)}",
                }
            )
    ext = schema_json.get("extractor")
    if ext is not None and ext not in SUPPORTED_EXTRACTORS:
        errs.append(
            {
                "field": "schema_json.extractor",
                "reason": f"unsupported extractor; allowed: {sorted(SUPPORTED_EXTRACTORS)}",
            }
        )
    return errs


def list_schemas(session: Session) -> list[ExtractionSchema]:
    return list(session.scalars(select(ExtractionSchema).order_by(ExtractionSchema.created_at)))


def get_schema(session: Session, schema_id: str) -> ExtractionSchema:
    s = session.get(ExtractionSchema, schema_id)
    if s is None:
        raise not_found("schema", schema_id)
    return s


def create_schema(
    session: Session, name: str, description: str, schema_json: dict | None, business_rules_json: dict | None
) -> ExtractionSchema:
    if session.scalar(select(ExtractionSchema).where(ExtractionSchema.name == name)):
        raise AppError("SCHEMA_EXISTS", f"schema '{name}' already exists", 409)
    schema = ExtractionSchema(name=name, description=description or "")
    session.add(schema)
    session.flush()
    if schema_json is not None:
        create_version(session, schema.id, schema_json, business_rules_json or {})
    return schema


def create_version(
    session: Session, schema_id: str, schema_json: dict, business_rules_json: dict, status: str = "published"
) -> ExtractionSchemaVersion:
    get_schema(session, schema_id)
    errs = validate_schema_json(schema_json)
    if errs:
        raise AppError("VALIDATION_ERROR", "schema is invalid", 422, errs)
    latest = (
        session.scalar(
            select(func.max(ExtractionSchemaVersion.version)).where(ExtractionSchemaVersion.schema_id == schema_id)
        )
        or 0
    )
    v = ExtractionSchemaVersion(
        schema_id=schema_id,
        version=latest + 1,
        schema_json=schema_json,
        business_rules_json=business_rules_json or {},
        status=status,
    )
    session.add(v)
    session.flush()
    return v


def resolve_version(session: Session, schema_version_id: str | None) -> ExtractionSchemaVersion:
    if schema_version_id is None:
        return ensure_builtin_schema(session)
    v = session.get(ExtractionSchemaVersion, schema_version_id)
    if v is None:
        raise not_found("schema", schema_version_id)
    if v.status != "published":
        raise AppError("SCHEMA_VERSION_NOT_PUBLISHED", "only published schema versions can be used", 422)
    if v.schema_json.get("extractor") not in SUPPORTED_EXTRACTORS:
        raise AppError("SCHEMA_NOT_SUPPORTED", "this schema has no supported extractor; use the rate_sheet schema", 422)
    return v
