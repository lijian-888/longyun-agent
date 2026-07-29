"""Controlled text-to-SQL templates for published rice research data.

The model never writes executable SQL. It can only fill a validated query plan;
this module chooses a fixed read-only PostgreSQL template and binds parameters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session


MAX_QUERY_ROWS = 100
MAX_VARIETY_MATCHES = 20
MAX_QUERY_TRAIT_CODES = 64
MAX_RESULT_OBSERVATIONS = 10_000
OPERATOR_SQL = {
    "eq": "=",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
}


@dataclass(frozen=True)
class SqlTemplate:
    """A documented, parameterized SQL template available to the query tool."""

    code: str
    title: str
    description: str
    parameters: tuple[str, ...]
    sql: str


VARIETY_LOOKUP_SQL = """
SELECT id, variety_name, normalized_name, alias_names, variety_type,
       approval_number, approval_year, suitable_region
FROM variety_basic
WHERE data_status = 'published'
ORDER BY variety_name
"""

PHENOTYPE_BY_VARIETY_SQL = """
SELECT v.id AS variety_id, v.variety_name, v.alias_names, v.variety_type,
       v.approval_number, v.approval_year, v.suitable_region,
       p.trait_code, p.trait_name, p.trait_category, p.value_numeric,
       p.value_text, p.unit, p.source_review_id, p.source_locator,
       p.trial_year, p.trial_location, p.evaluation_method
FROM variety_basic v
JOIN phenotype_observation p ON p.variety_id = v.id
WHERE v.data_status = 'published'
  AND p.publish_status = 'published'
  AND v.id = ANY(CAST(:variety_ids AS text[]))
  AND (:trait_codes_empty OR p.trait_code = ANY(CAST(:trait_codes AS text[])))
ORDER BY v.variety_name, p.trait_code
LIMIT :row_limit
"""

ROOT_BY_VARIETY_SQL = """
SELECT v.id AS variety_id, v.variety_name, v.alias_names, v.variety_type,
       v.approval_number, v.approval_year, v.suitable_region,
       r.trait_code, r.trait_name, r.trait_category, r.value_numeric,
       r.value_text, r.unit, r.source_review_id, r.source_locator,
       NULL::text AS trial_year, NULL::text AS trial_location,
       NULL::text AS evaluation_method
FROM variety_basic v
JOIN root_phenotype_observation r ON r.variety_id = v.id
WHERE v.data_status = 'published'
  AND v.id = ANY(CAST(:variety_ids AS text[]))
  AND (:trait_codes_empty OR r.trait_code = ANY(CAST(:trait_codes AS text[])))
ORDER BY v.variety_name, r.trait_code
LIMIT :row_limit
"""

PHENOTYPE_BY_TRAIT_SQL = """
WITH matched_varieties AS (
    SELECT DISTINCT v.id, v.variety_name
    FROM variety_basic v
    JOIN phenotype_observation p ON p.variety_id = v.id
    WHERE v.data_status = 'published'
      AND p.publish_status = 'published'
      AND p.trait_code = ANY(CAST(:trait_codes AS text[]))
    ORDER BY v.variety_name
    LIMIT :limit
)
SELECT v.id AS variety_id, v.variety_name, v.alias_names, v.variety_type,
       v.approval_number, v.approval_year, v.suitable_region,
       p.trait_code, p.trait_name, p.trait_category, p.value_numeric,
       p.value_text, p.unit, p.source_review_id, p.source_locator,
       p.trial_year, p.trial_location, p.evaluation_method
FROM variety_basic v
JOIN matched_varieties matched ON matched.id = v.id
JOIN phenotype_observation p ON p.variety_id = v.id
WHERE v.data_status = 'published'
  AND p.publish_status = 'published'
  AND p.trait_code = ANY(CAST(:trait_codes AS text[]))
ORDER BY v.variety_name, p.trait_code
LIMIT :row_limit
"""

ROOT_BY_TRAIT_SQL = """
WITH matched_varieties AS (
    SELECT DISTINCT v.id, v.variety_name
    FROM variety_basic v
    JOIN root_phenotype_observation r ON r.variety_id = v.id
    WHERE v.data_status = 'published'
      AND r.trait_code = ANY(CAST(:trait_codes AS text[]))
    ORDER BY v.variety_name
    LIMIT :limit
)
SELECT v.id AS variety_id, v.variety_name, v.alias_names, v.variety_type,
       v.approval_number, v.approval_year, v.suitable_region,
       r.trait_code, r.trait_name, r.trait_category, r.value_numeric,
       r.value_text, r.unit, r.source_review_id, r.source_locator,
       NULL::text AS trial_year, NULL::text AS trial_location,
       NULL::text AS evaluation_method
FROM variety_basic v
JOIN matched_varieties matched ON matched.id = v.id
JOIN root_phenotype_observation r ON r.variety_id = v.id
WHERE v.data_status = 'published'
  AND r.trait_code = ANY(CAST(:trait_codes AS text[]))
ORDER BY v.variety_name, r.trait_code
LIMIT :row_limit
"""


SQL_TEMPLATES = {
    "phenotype_by_variety": SqlTemplate(
        code="phenotype_by_variety",
        title="按品种查询水稻表型",
        description="查询指定品种或别名的已发布表型字段；字段为空时返回该品种全部已发布表型。",
        parameters=("variety_ids", "trait_codes", "trait_codes_empty", "row_limit"),
        sql=PHENOTYPE_BY_VARIETY_SQL.strip(),
    ),
    "root_by_variety": SqlTemplate(
        code="root_by_variety",
        title="按品种查询根系表型",
        description="查询指定品种的已发布根系表型字段。",
        parameters=("variety_ids", "trait_codes", "trait_codes_empty", "row_limit"),
        sql=ROOT_BY_VARIETY_SQL.strip(),
    ),
    "phenotype_by_trait": SqlTemplate(
        code="phenotype_by_trait",
        title="按字段查询水稻表型",
        description="查询全部已发布品种的指定水稻表型字段。",
        parameters=("trait_codes", "limit", "row_limit"),
        sql=PHENOTYPE_BY_TRAIT_SQL.strip(),
    ),
    "root_by_trait": SqlTemplate(
        code="root_by_trait",
        title="按字段查询根系表型",
        description="查询全部已发布品种的指定根系表型字段。",
        parameters=("trait_codes", "limit", "row_limit"),
        sql=ROOT_BY_TRAIT_SQL.strip(),
    ),
    "phenotype_filter": SqlTemplate(
        code="phenotype_filter",
        title="按多个表型条件筛选品种",
        description="由后端以多个固定 EXISTS 子句组合条件；操作符和字段都经过白名单校验。",
        parameters=("condition trait_code/value/operator", "variety_ids", "limit"),
        sql="由后端根据已验证的数值条件拼接固定 EXISTS 子句，不接受模型提供的 SQL 文本。",
    ),
}


class NumericFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trait_code: str
    operator: Literal["eq", "lt", "lte", "gt", "gte"]
    value: float


class PublishedDataQuery(BaseModel):
    """The only query plan shape accepted from natural-language interpretation."""

    scope: Literal["rice_phenotype", "root_phenotype"] = "rice_phenotype"
    variety_ids: list[str] = Field(default_factory=list, max_length=MAX_VARIETY_MATCHES)
    trait_codes: list[str] = Field(default_factory=list, max_length=MAX_QUERY_TRAIT_CODES)
    filters: list[NumericFilter] = Field(default_factory=list, max_length=6)
    limit: int = Field(default=MAX_QUERY_ROWS, ge=1, le=MAX_QUERY_ROWS)


class StructuredQueryRequest(BaseModel):
    """Safe planner output accepted from an LLM fallback, never SQL text."""

    model_config = ConfigDict(extra="forbid")

    query_needed: bool = False
    scope: Literal["rice_phenotype", "root_phenotype"] = "rice_phenotype"
    variety_names: list[str] = Field(default_factory=list, max_length=MAX_VARIETY_MATCHES)
    trait_codes: list[str] = Field(default_factory=list, max_length=MAX_QUERY_TRAIT_CODES)
    filters: list[NumericFilter] = Field(default_factory=list, max_length=6)
    clarification: str | None = Field(default=None, max_length=300)


@dataclass
class QueryExecution:
    template_code: str
    parameters: dict[str, Any]
    records: list[dict[str, Any]]
    matched_variety_names: list[str] = field(default_factory=list)
    unresolved_variety_names: list[str] = field(default_factory=list)
    unresolved_field_terms: list[str] = field(default_factory=list)


def template_catalog() -> list[dict[str, Any]]:
    """Safe metadata for inspecting available query templates without exposing DB access."""
    return [
        {
            "code": item.code,
            "title": item.title,
            "description": item.description,
            "parameters": list(item.parameters),
            "sql": item.sql,
        }
        for item in SQL_TEMPLATES.values()
    ]


def field_catalog_for_planner(
    rice_traits: dict[str, dict[str, Any]],
    root_traits: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only governed field metadata for a structured-query planner."""
    fields = []
    for scope, catalog in (("rice_phenotype", rice_traits), ("root_phenotype", root_traits)):
        for code, trait in catalog.items():
            fields.append({
                "scope": scope,
                "trait_code": code,
                "name": trait.get("name"),
                "aliases": trait.get("aliases") or [],
                "unit": trait.get("unit") or "",
            })
    return fields


def is_likely_data_query(question: str) -> bool:
    normalized = _normalize(question)
    markers = ("查询", "检索", "筛选", "比较", "对比", "排名", "最高", "最低", "平均", "统计", "字段", "表型", "品种")
    return any(marker in normalized for marker in markers)


def plan_query_from_question(
    session: Session,
    question: str,
    rice_traits: dict[str, dict[str, Any]],
    root_traits: dict[str, dict[str, Any]],
) -> PublishedDataQuery | None:
    """Deterministic first-pass intent extraction from names, aliases, and field dictionary."""
    normalized_question = _normalize(question)
    is_root_question = any(token in normalized_question for token in ("根系", "根长", "根数", "根表面积", "根体积", "根干重", "根冠比", "根角"))
    catalog = root_traits if is_root_question else rice_traits
    trait_codes = _match_trait_codes(normalized_question, catalog)
    filters = _extract_filters(question, catalog)
    trait_codes = list(dict.fromkeys([*trait_codes, *(item.trait_code for item in filters)]))
    varieties = session.execute(text(VARIETY_LOOKUP_SQL)).mappings().all()
    matched_ids: list[str] = []
    for variety in varieties:
        names = [variety["variety_name"], *(variety["alias_names"] or [])]
        if any(name and _normalize(str(name)) in normalized_question for name in names):
            matched_ids.append(str(variety["id"]))

    if not matched_ids and not trait_codes and not filters:
        return None
    return PublishedDataQuery(
        scope="root_phenotype" if is_root_question else "rice_phenotype",
        variety_ids=list(dict.fromkeys(matched_ids)),
        trait_codes=trait_codes,
        filters=filters,
    )


def plan_query_from_structured_request(
    session: Session,
    request: StructuredQueryRequest,
    rice_traits: dict[str, dict[str, Any]],
    root_traits: dict[str, dict[str, Any]],
) -> tuple[PublishedDataQuery | None, list[str]]:
    """Resolve an LLM-safe request against governed fields and published varieties."""
    if not request.query_needed:
        return None, []
    catalog = root_traits if request.scope == "root_phenotype" else rice_traits
    trait_codes = [code for code in request.trait_codes if code in catalog]
    filters = [item for item in request.filters if item.trait_code in catalog]
    trait_codes = list(dict.fromkeys([*trait_codes, *(item.trait_code for item in filters)]))
    variety_ids, unresolved_names = _resolve_variety_names(session, request.variety_names)
    # A supplied but wholly unresolved name must never broaden into a whole-
    # dataset query merely because trait fields were also selected.
    if request.variety_names and not variety_ids:
        return None, unresolved_names
    if not variety_ids and not trait_codes and not filters:
        return None, unresolved_names
    return PublishedDataQuery(
        scope=request.scope,
        variety_ids=variety_ids,
        trait_codes=trait_codes,
        filters=filters,
    ), unresolved_names


def execute_published_data_query(session: Session, query: PublishedDataQuery) -> QueryExecution:
    """Execute a validated plan using only fixed read-only SQL templates."""
    query = _validated_query(query)
    if query.filters:
        return _execute_filter_query(session, query)

    is_root = query.scope == "root_phenotype"
    if query.variety_ids:
        template_code = "root_by_variety" if is_root else "phenotype_by_variety"
        params = {
            "variety_ids": query.variety_ids,
            "trait_codes": query.trait_codes,
            "trait_codes_empty": not bool(query.trait_codes),
            "row_limit": _observation_row_limit(query),
        }
    elif query.trait_codes:
        template_code = "root_by_trait" if is_root else "phenotype_by_trait"
        params = {
            "trait_codes": query.trait_codes,
            "limit": query.limit,
            "row_limit": _observation_row_limit(query),
        }
    else:
        return QueryExecution(template_code="", parameters={}, records=[])

    rows = session.execute(text(SQL_TEMPLATES[template_code].sql), params).mappings().all()
    return QueryExecution(
        template_code=template_code,
        parameters=_safe_parameters(params),
        records=[dict(row) for row in rows],
        matched_variety_names=_names_for_ids(session, query.variety_ids),
    )


def _execute_filter_query(session: Session, query: PublishedDataQuery) -> QueryExecution:
    table_name = "root_phenotype_observation" if query.scope == "root_phenotype" else "phenotype_observation"
    published_clause = "" if query.scope == "root_phenotype" else "AND p.publish_status = 'published'"
    clauses: list[str] = ["v.data_status = 'published'"]
    params: dict[str, Any] = {"limit": query.limit}
    if query.variety_ids:
        clauses.append("v.id = ANY(CAST(:variety_ids AS text[]))")
        params["variety_ids"] = query.variety_ids
    for index, item in enumerate(query.filters):
        params[f"trait_code_{index}"] = item.trait_code
        params[f"value_{index}"] = item.value
        operator = OPERATOR_SQL[item.operator]
        clauses.append(
            f"""EXISTS (
                SELECT 1 FROM {table_name} p{index}
                WHERE p{index}.variety_id = v.id
                  {published_clause.replace('p.', f'p{index}.')}
                  AND p{index}.trait_code = :trait_code_{index}
                  AND p{index}.value_numeric {operator} :value_{index}
            )"""
        )
    variety_rows = session.execute(
        text(
            "SELECT v.id FROM variety_basic v WHERE "
            + " AND ".join(clauses)
            + " ORDER BY v.variety_name LIMIT :limit"
        ),
        params,
    ).mappings().all()
    variety_ids = [str(row["id"]) for row in variety_rows]
    detail_query = query.model_copy(update={
        "variety_ids": variety_ids,
        "trait_codes": list(dict.fromkeys([*query.trait_codes, *(item.trait_code for item in query.filters)])),
        "filters": [],
    })
    detail_execution = execute_published_data_query(session, detail_query)
    detail_execution.template_code = "root_filter" if query.scope == "root_phenotype" else "phenotype_filter"
    detail_execution.parameters = _safe_parameters(params)
    return detail_execution


def _observation_row_limit(query: PublishedDataQuery) -> int:
    """A query limit represents varieties, not individual long-table observations."""
    requested_traits = len(query.trait_codes) or MAX_QUERY_TRAIT_CODES
    return min(query.limit * requested_traits, MAX_RESULT_OBSERVATIONS)


def _validated_query(query: PublishedDataQuery) -> PublishedDataQuery:
    allowed = set(OPERATOR_SQL)
    for item in query.filters:
        if item.operator not in allowed:
            raise ValueError("不支持的数值筛选操作符")
    return query


def _match_trait_codes(question: str, catalog: dict[str, dict[str, Any]]) -> list[str]:
    matched: list[tuple[int, str, str]] = []
    for code, trait in catalog.items():
        for name in [trait.get("name", ""), *(trait.get("aliases") or [])]:
            normalized = _normalize(str(name))
            if len(normalized) >= 2 and normalized in question:
                matched.append((len(normalized), normalized, code))
                break
    selected_codes: list[str] = []
    selected_terms: list[str] = []
    for _, term, code in sorted(matched, reverse=True):
        if code in selected_codes or any(term in selected for selected in selected_terms):
            continue
        selected_codes.append(code)
        selected_terms.append(term)
    return selected_codes


def _extract_filters(question: str, catalog: dict[str, dict[str, Any]]) -> list[NumericFilter]:
    filters: list[NumericFilter] = []
    normalized = _normalize(question)
    operator_patterns = [
        ("lte", r"(?:不超过|不高于|至多|≤|<=)\s*(-?\d+(?:\.\d+)?)"),
        ("gte", r"(?:不少于|不低于|至少|≥|>=)\s*(-?\d+(?:\.\d+)?)"),
        ("lt", r"(?:小于|低于|少于|<)\s*(-?\d+(?:\.\d+)?)"),
        ("gt", r"(?:大于|高于|多于|>)\s*(-?\d+(?:\.\d+)?)"),
        ("eq", r"(?:等于|为|=)\s*(-?\d+(?:\.\d+)?)"),
    ]
    for code, trait in catalog.items():
        names = [_normalize(str(name)) for name in [trait.get("name", ""), *(trait.get("aliases") or [])]]
        position = next((normalized.find(name) for name in names if name and normalized.find(name) >= 0), -1)
        if position < 0:
            continue
        fragment = normalized[position: position + 48]
        for operator, pattern in operator_patterns:
            match = re.search(pattern, fragment)
            if match:
                filters.append(NumericFilter(trait_code=code, operator=operator, value=float(match.group(1))))
                break
    return filters


def _names_for_ids(session: Session, variety_ids: list[str]) -> list[str]:
    if not variety_ids:
        return []
    rows = session.execute(
        text("SELECT variety_name FROM variety_basic WHERE id = ANY(CAST(:variety_ids AS text[])) ORDER BY variety_name"),
        {"variety_ids": variety_ids},
    ).mappings().all()
    return [str(row["variety_name"]) for row in rows]


def _resolve_variety_names(session: Session, names: list[str]) -> tuple[list[str], list[str]]:
    if not names:
        return [], []
    varieties = session.execute(text(VARIETY_LOOKUP_SQL)).mappings().all()
    resolved: list[str] = []
    unresolved: list[str] = []
    for requested in names:
        normalized_requested = _normalize(requested)
        match = next((
            variety for variety in varieties
            if normalized_requested and any(
                _normalize(str(candidate)) == normalized_requested
                for candidate in [variety["variety_name"], *(variety["alias_names"] or [])]
            )
        ), None)
        if match:
            resolved.append(str(match["id"]))
        else:
            unresolved.append(requested)
    return list(dict.fromkeys(resolved)), unresolved


def _normalize(value: str) -> str:
    return re.sub(r"[\s()（）\-_/，,。；;：:]", "", value or "").lower()


def _safe_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Keep result cards traceable without embedding the user's original question."""
    return {key: value for key, value in parameters.items() if key != "variety_ids"}
