"""Allowlisted, project-scoped structured queries for research users.

This module deliberately exposes business concepts instead of database table
names.  Every dataset and field expression is defined in code, so a browser
cannot submit SQL, choose an arbitrary table, or escape the active project.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session


FieldKind = Literal["text", "number", "integer", "boolean", "date", "datetime", "json"]


class ProjectDataQueryError(ValueError):
    """Raised when a requested dataset, field or filter is not allowlisted."""


@dataclass(frozen=True)
class ProjectDataField:
    code: str
    name: str
    expression: str
    kind: FieldKind = "text"
    unit: str | None = None
    default: bool = True
    searchable: bool = False
    filterable: bool = True


@dataclass(frozen=True)
class ProjectDataSet:
    code: str
    title: str
    description: str
    from_sql: str
    project_predicate: str
    row_id_expression: str
    order_by: str
    fields: tuple[ProjectDataField, ...]


def _field(
    code: str,
    name: str,
    expression: str,
    kind: FieldKind = "text",
    *,
    unit: str | None = None,
    default: bool = True,
    searchable: bool = False,
    filterable: bool = True,
) -> ProjectDataField:
    return ProjectDataField(
        code=code,
        name=name,
        expression=expression,
        kind=kind,
        unit=unit,
        default=default,
        searchable=searchable,
        filterable=filterable,
    )


PROJECT_DATASETS: tuple[ProjectDataSet, ...] = (
    ProjectDataSet(
        code="germplasm",
        title="种质材料",
        description="当前课题已建立关联的材料主档、别名和系谱摘要。",
        from_sql="""
            breeding_material material
            JOIN data_material_project_scope scope ON scope.material_id=material.id
        """,
        project_predicate="scope.project_id=:project_id",
        row_id_expression="material.id",
        order_by="material.material_code, material.material_name",
        fields=(
            _field("material_code", "材料编号", "material.material_code", searchable=True),
            _field("material_name", "材料名称", "material.material_name", searchable=True),
            _field("material_type", "材料类型", "material.material_type", searchable=True),
            _field("is_check", "是否对照", "material.is_check", "boolean"),
            _field("aliases", "材料别名", "material.aliases", "json", searchable=True, filterable=False),
            _field("pedigree_summary", "系谱摘要", "material.pedigree_summary", searchable=True),
        ),
    ),
    ProjectDataSet(
        code="pedigree",
        title="系谱关系",
        description="当前课题材料的亲本—后代关系、亲本角色和组合依据。",
        from_sql="""
            breeding_pedigree_relationship relationship
            JOIN breeding_material child ON child.id=relationship.child_material_id
            JOIN breeding_material parent ON parent.id=relationship.parent_material_id
        """,
        project_predicate="relationship.project_id=:project_id",
        row_id_expression="relationship.id",
        order_by="child.material_code, relationship.parent_role, parent.material_code",
        fields=(
            _field("child_code", "后代材料编号", "child.material_code", searchable=True),
            _field("child_name", "后代材料名称", "child.material_name", searchable=True),
            _field("parent_code", "亲本材料编号", "parent.material_code", searchable=True),
            _field("parent_name", "亲本材料名称", "parent.material_name", searchable=True),
            _field("parent_role", "亲本角色", "relationship.parent_role", searchable=True),
            _field("relationship_type", "关系类型", "relationship.relationship_type", searchable=True),
            _field("parent_origin", "亲本来源", "relationship.parent_origin", searchable=True),
            _field("parent_trait_summary", "亲本性状摘要", "relationship.parent_trait_summary", searchable=True),
            _field("combination_basis", "组合依据", "relationship.combination_basis", searchable=True),
        ),
    ),
    ProjectDataSet(
        code="trial_phenotype",
        title="田间试验与表型",
        description="按试验、地点、处理、小区和材料关联的表型观测记录。",
        from_sql="""
            trial_phenotype_observation observation
            JOIN trial_entry entry ON entry.id=observation.entry_id
            JOIN field_trial trial ON trial.id=entry.trial_id
            JOIN trial_site site ON site.id=trial.site_id
            JOIN trial_treatment treatment ON treatment.id=entry.treatment_id
            JOIN breeding_material material ON material.id=entry.material_id
        """,
        project_predicate="trial.project_id=:project_id",
        row_id_expression="observation.id",
        order_by="trial.trial_year DESC, trial.trial_code, entry.plot_no, observation.trait_code",
        fields=(
            _field("trial_code", "试验编号", "trial.trial_code", searchable=True),
            _field("trial_name", "试验名称", "trial.trial_name", searchable=True),
            _field("trial_year", "试验年份", "trial.trial_year", "integer"),
            _field("site_name", "试验地点", "site.site_name", searchable=True),
            _field("ecological_zone", "生态区", "site.ecological_zone", searchable=True, default=False),
            _field("treatment_name", "试验处理", "treatment.treatment_name", searchable=True),
            _field("material_code", "材料编号", "material.material_code", searchable=True),
            _field("material_name", "材料名称", "material.material_name", searchable=True),
            _field("replicate_no", "重复号", "entry.replicate_no", "integer"),
            _field("block_no", "区组号", "entry.block_no", "integer", default=False),
            _field("plot_no", "小区号", "entry.plot_no", searchable=True),
            _field("trait_code", "性状代码", "observation.trait_code", searchable=True),
            _field("trait_name", "性状名称", "observation.trait_name", searchable=True),
            _field("value_numeric", "数值观测值", "observation.value_numeric", "number"),
            _field("value_text", "文本观测值", "observation.value_text", default=False),
            _field("unit", "单位", "observation.unit", searchable=True),
            _field("observation_stage", "观测时期", "observation.observation_stage", searchable=True),
            _field("quality_status", "质量状态", "observation.quality_status", searchable=True, default=False),
            _field("publish_status", "数据状态", "observation.publish_status", searchable=True, default=False),
        ),
    ),
    ProjectDataSet(
        code="environment",
        title="环境指标",
        description="当前课题试验地点关联的气象、土壤及其他环境测量指标。",
        from_sql="""
            trial_environment_metric metric
            JOIN field_trial trial ON trial.id=metric.trial_id
            JOIN trial_site site ON site.id=trial.site_id
        """,
        project_predicate="trial.project_id=:project_id",
        row_id_expression="metric.id",
        order_by="trial.trial_year DESC, trial.trial_code, metric.metric_code",
        fields=(
            _field("trial_code", "试验编号", "trial.trial_code", searchable=True),
            _field("trial_name", "试验名称", "trial.trial_name", searchable=True),
            _field("trial_year", "试验年份", "trial.trial_year", "integer"),
            _field("site_name", "试验地点", "site.site_name", searchable=True),
            _field("metric_code", "指标代码", "metric.metric_code", searchable=True),
            _field("metric_name", "指标名称", "metric.metric_name", searchable=True),
            _field("value_numeric", "指标值", "metric.value_numeric", "number"),
            _field("unit", "单位", "metric.unit", searchable=True),
            _field("collection_method", "采集方法", "metric.collection_method", searchable=True),
        ),
    ),
    ProjectDataSet(
        code="management",
        title="栽培管理",
        description="当前课题各试验处理关联的施肥、灌溉和其他田间管理事件。",
        from_sql="""
            trial_management_event event
            JOIN trial_treatment treatment ON treatment.id=event.treatment_id
            JOIN field_trial trial ON trial.id=treatment.trial_id
            JOIN trial_site site ON site.id=trial.site_id
        """,
        project_predicate="trial.project_id=:project_id",
        row_id_expression="event.id",
        order_by="trial.trial_year DESC, trial.trial_code, treatment.treatment_code, event.event_type",
        fields=(
            _field("trial_code", "试验编号", "trial.trial_code", searchable=True),
            _field("trial_name", "试验名称", "trial.trial_name", searchable=True),
            _field("trial_year", "试验年份", "trial.trial_year", "integer"),
            _field("site_name", "试验地点", "site.site_name", searchable=True),
            _field("treatment_code", "处理编号", "treatment.treatment_code", searchable=True),
            _field("treatment_name", "处理名称", "treatment.treatment_name", searchable=True),
            _field("event_type", "管理类型", "event.event_type", searchable=True),
            _field("input_name", "投入品或措施", "event.input_name", searchable=True),
            _field("rate_per_mu", "亩用量", "event.rate_per_mu", "number"),
            _field("unit", "单位", "event.unit", searchable=True),
            _field("event_stage", "实施时期", "event.event_stage", searchable=True),
            _field("notes", "备注", "event.notes", searchable=True, default=False),
        ),
    ),
    ProjectDataSet(
        code="genotype_assets",
        title="基因型数据集",
        description="基因型数据集、当前质控版本和样本映射统计；不直接返回原始变异矩阵。",
        from_sql="""
            genotype_asset asset
            LEFT JOIN genotype_asset_version version ON version.id=asset.current_version_id
        """,
        project_predicate="asset.project_id=:project_id",
        row_id_expression="asset.id",
        order_by="asset.updated_at DESC, asset.title",
        fields=(
            _field("title", "数据集名称", "asset.title", searchable=True),
            _field("source_format", "源数据格式", "asset.source_format", searchable=True),
            _field("reference_assembly", "参考基因组", "asset.reference_assembly", searchable=True),
            _field("population_type", "群体类型", "asset.population_type", searchable=True),
            _field("asset_status", "数据集状态", "asset.status", searchable=True),
            _field("version_number", "当前版本", "version.version_number", "integer"),
            _field("version_status", "质控状态", "version.status", searchable=True),
            _field(
                "sample_count",
                "样本数",
                "(SELECT count(*) FROM genotype_sample_mapping mapping WHERE mapping.version_id=version.id)",
                "integer",
            ),
            _field(
                "mapped_sample_count",
                "已关联材料样本数",
                "(SELECT count(*) FROM genotype_sample_mapping mapping WHERE mapping.version_id=version.id AND mapping.material_id IS NOT NULL)",
                "integer",
            ),
            _field("updated_at", "更新时间", "asset.updated_at", "datetime", filterable=False),
        ),
    ),
)


_DATASET_BY_CODE = {dataset.code: dataset for dataset in PROJECT_DATASETS}


def project_data_catalog() -> dict[str, Any]:
    return {
        "datasets": [
            {
                "code": dataset.code,
                "title": dataset.title,
                "description": dataset.description,
                "fields": [
                    {
                        "code": field.code,
                        "name": field.name,
                        "kind": field.kind,
                        "unit": field.unit,
                        "default": field.default,
                        "searchable": field.searchable,
                        "filterable": field.filterable,
                    }
                    for field in dataset.fields
                ],
            }
            for dataset in PROJECT_DATASETS
        ]
    }


def _coerce_filter_value(field: ProjectDataField, value: Any) -> Any:
    if field.kind == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ProjectDataQueryError(f"{field.name}需要填写数值。") from exc
    if field.kind == "integer":
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ProjectDataQueryError(f"{field.name}需要填写整数。") from exc
        if not number.is_integer():
            raise ProjectDataQueryError(f"{field.name}需要填写整数。")
        return int(number)
    if field.kind == "boolean":
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "是"}:
            return True
        if normalized in {"false", "0", "no", "否"}:
            return False
        raise ProjectDataQueryError(f"{field.name}需要选择是或否。")
    return str(value).strip()


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def query_project_data(
    session: Session,
    *,
    project_id: str,
    dataset_code: str,
    selected_field_codes: list[str],
    search: str,
    filters: list[dict[str, Any]],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    dataset = _DATASET_BY_CODE.get(dataset_code)
    if not dataset:
        raise ProjectDataQueryError("不支持所选课题数据类型。")
    fields_by_code = {field.code: field for field in dataset.fields}
    requested_codes = list(dict.fromkeys(selected_field_codes))
    if not requested_codes:
        requested_codes = [field.code for field in dataset.fields if field.default]
    unknown_fields = [code for code in requested_codes if code not in fields_by_code]
    if unknown_fields:
        raise ProjectDataQueryError("查询包含未开放字段。")
    selected_fields = [fields_by_code[code] for code in requested_codes]

    predicates = [dataset.project_predicate]
    parameters: dict[str, Any] = {"project_id": project_id, "limit": limit + 1, "offset": offset}
    keyword = search.strip()
    searchable_fields = [field for field in dataset.fields if field.searchable]
    if keyword and searchable_fields:
        predicates.append("(" + " OR ".join(
            f"CAST({field.expression} AS TEXT) ILIKE :search_pattern"
            for field in searchable_fields
        ) + ")")
        parameters["search_pattern"] = f"%{keyword}%"

    numeric_operators = {"eq": "=", "ne": "<>", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}
    text_operators = {"eq": "=", "ne": "<>"}
    for index, item in enumerate(filters):
        field_code = str(item.get("field") or "")
        field = fields_by_code.get(field_code)
        if not field or not field.filterable:
            raise ProjectDataQueryError("筛选条件包含未开放字段。")
        operator = str(item.get("operator") or "eq")
        parameter_name = f"filter_{index}"
        if operator == "contains":
            if field.kind not in {"text", "json"}:
                raise ProjectDataQueryError(f"{field.name}不支持包含筛选。")
            predicates.append(f"CAST({field.expression} AS TEXT) ILIKE :{parameter_name}")
            parameters[parameter_name] = f"%{str(item.get('value') or '').strip()}%"
            continue
        allowed = numeric_operators if field.kind in {"number", "integer", "date", "datetime"} else text_operators
        sql_operator = allowed.get(operator)
        if not sql_operator:
            raise ProjectDataQueryError(f"{field.name}不支持所选筛选方式。")
        predicates.append(f"{field.expression} {sql_operator} :{parameter_name}")
        parameters[parameter_name] = _coerce_filter_value(field, item.get("value"))

    select_sql = ", ".join(
        [f"{dataset.row_id_expression} AS _row_id"]
        + [f"{field.expression} AS {field.code}" for field in selected_fields]
    )
    statement = text(f"""
        SELECT {select_sql}
        FROM {dataset.from_sql}
        WHERE {' AND '.join(predicates)}
        ORDER BY {dataset.order_by}
        LIMIT :limit OFFSET :offset
    """)
    rows = session.execute(statement, parameters).mappings().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "dataset": dataset.code,
        "dataset_title": dataset.title,
        "project_id": project_id,
        "offset": offset,
        "limit": limit,
        "record_count": len(rows),
        "has_more": has_more,
        "fields": [
            {"code": field.code, "name": field.name, "kind": field.kind, "unit": field.unit}
            for field in selected_fields
        ],
        "records": [
            {
                "id": str(row["_row_id"]),
                **{
                    field.code: _serialize_value(row[field.code])
                    for field in selected_fields
                },
            }
            for row in rows
        ],
    }
