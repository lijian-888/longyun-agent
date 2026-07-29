"""Controlled local statistics for governed regional-trial data.

The assistant never creates arbitrary SQL or statistical formulas.  This
module receives already-published plot-level records and selects from a small
set of reviewed analysis procedures.  Every completed analysis stores its
filters, formula, engine version, source trials and result payload so a
researcher can trace a conclusion back to the governed data package.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Iterable
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
from statsmodels.formula.api import ols
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multicomp import pairwise_tukeyhsd


ANALYSIS_ENGINE_NAME = "本地统计引擎（statsmodels）"
ANALYSIS_VERSION = "rcbd-v1.0"
STANDARD_TREATMENT = "M1"
HIGH_NITROGEN_TREATMENT = "M2"


class TrialStatisticsError(ValueError):
    """The published package lacks the minimum data for a requested analysis."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _native(value: Any) -> Any:
    """Convert pandas/numpy values into JSON-safe ordinary Python objects."""
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _native(value.item())
        except (ValueError, TypeError):
            pass
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _key(value: Any) -> str:
    return re.sub(r"[\s()（）\[\]【】_\-—:/：,.，]+", "", str(value or "")).lower()


def _question_year(question: str) -> int | None:
    match = re.search(r"(20\d{2})", question)
    return int(match.group(1)) if match else None


def _question_treatment(question: str) -> str:
    normalized = _key(question)
    if any(token in normalized for token in ("高氮", "较高施氮", "m2", "高施氮")):
        return HIGH_NITROGEN_TREATMENT
    return STANDARD_TREATMENT


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    numeric_value = float(value)
    return round(numeric_value, digits) if math.isfinite(numeric_value) else None


def _anova_rows(model: Any) -> list[dict[str, Any]]:
    table = anova_lm(model, typ=2).reset_index().rename(columns={"index": "factor"})
    records: list[dict[str, Any]] = []
    for _, row in table.iterrows():
        records.append({
            "factor": str(row["factor"]),
            "sum_sq": _round(row.get("sum_sq")),
            "df": _round(row.get("df"), 2),
            "f_value": _round(row.get("F")),
            "p_value": _round(row.get("PR(>F)"), 6),
        })
    return records


def _frame(session: Session, package_id: str) -> pd.DataFrame:
    """Pivot the governed long phenotype table back to a plot-level analysis frame."""
    rows = session.execute(text("""
        SELECT
            trial.id AS trial_id,
            trial.trial_code,
            trial.trial_year,
            site.site_code,
            site.site_name,
            site.ecological_zone,
            treatment.treatment_code,
            treatment.treatment_name,
            entry.id AS entry_id,
            entry.replicate_no,
            entry.block_no,
            entry.plot_no,
            material.material_code,
            material.material_name,
            material.is_check,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'yield_per_mu') AS yield_per_mu,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'plant_height') AS plant_height,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'thousand_grain_weight') AS thousand_grain_weight,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'seed_setting_rate') AS seed_setting_rate,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'head_rice_rate') AS head_rice_rate,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'chalkiness_degree') AS chalkiness_degree,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'panicle_blast_score') AS panicle_blast_score,
            MAX(observation.value_numeric) FILTER (WHERE observation.trait_code = 'lodging_score') AS lodging_score
        FROM field_trial trial
        JOIN trial_site site ON site.id = trial.site_id
        JOIN trial_treatment treatment ON treatment.trial_id = trial.id
        JOIN trial_entry entry ON entry.trial_id = trial.id AND entry.treatment_id = treatment.id
        JOIN breeding_material material ON material.id = entry.material_id
        JOIN trial_phenotype_observation observation ON observation.entry_id = entry.id
        WHERE trial.package_id = :package_id
          AND trial.data_status = 'published'
          AND observation.publish_status = 'published'
        GROUP BY trial.id, trial.trial_code, trial.trial_year, site.site_code, site.site_name,
                 site.ecological_zone, treatment.treatment_code, treatment.treatment_name,
                 entry.id, entry.replicate_no, entry.block_no, entry.plot_no,
                 material.material_code, material.material_name, material.is_check
        ORDER BY trial.trial_year, site.site_code, treatment.treatment_code, entry.block_no, material.material_code
    """), {"package_id": package_id}).mappings().all()
    data = pd.DataFrame([dict(row) for row in rows])
    if data.empty:
        raise TrialStatisticsError("该已发布资料包没有可用于统计分析的小区级表型观测")
    for column in ("yield_per_mu", "plant_height", "thousand_grain_weight", "seed_setting_rate", "head_rice_rate", "chalkiness_degree", "panicle_blast_score", "lodging_score"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data


def _environment_frame(session: Session, package_id: str) -> pd.DataFrame:
    rows = session.execute(text("""
        SELECT
            trial.id AS trial_id,
            MAX(metric.value_numeric) FILTER (WHERE metric.metric_code = 'soil_ph') AS soil_ph,
            MAX(metric.value_numeric) FILTER (WHERE metric.metric_code = 'available_phosphorus') AS available_phosphorus,
            MAX(metric.value_numeric) FILTER (WHERE metric.metric_code = 'organic_matter') AS organic_matter,
            MAX(metric.value_numeric) FILTER (WHERE metric.metric_code = 'rainfall') AS rainfall,
            MAX(metric.value_numeric) FILTER (WHERE metric.metric_code = 'mean_temperature') AS mean_temperature,
            MAX(metric.value_numeric) FILTER (WHERE metric.metric_code = 'disease_pressure') AS disease_pressure
        FROM field_trial trial
        LEFT JOIN trial_environment_metric metric ON metric.trial_id = trial.id
        WHERE trial.package_id = :package_id AND trial.data_status = 'published'
        GROUP BY trial.id
    """), {"package_id": package_id}).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def _find_site(data: pd.DataFrame, question: str) -> str | None:
    normalized = _key(question)
    for site_name in data["site_name"].dropna().unique():
        site_key = _key(site_name)
        short_key = re.sub(r"(试验点|试验站|试验基地|点)$", "", site_key)
        if site_key in normalized or (short_key and short_key in normalized):
            return str(site_name)
    for site_code in data["site_code"].dropna().unique():
        if _key(site_code) in normalized:
            names = data.loc[data["site_code"] == site_code, "site_name"].dropna().unique()
            return str(names[0]) if len(names) else None
    return None


def _select_single_trial(data: pd.DataFrame, question: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    year = _question_year(question) or int(data["trial_year"].max())
    site = _find_site(data, question)
    candidates = data.loc[data["trial_year"] == year]
    if site:
        candidates = candidates.loc[candidates["site_name"] == site]
    if candidates.empty:
        raise TrialStatisticsError(f"已发布资料中没有 {year} 年{site or ''}的区域试验记录")
    trial_ids = candidates["trial_id"].dropna().unique().tolist()
    if len(trial_ids) != 1:
        choices = candidates[["trial_year", "site_name"]].drop_duplicates().to_dict("records")
        raise TrialStatisticsError(f"问题未能唯一定位一个试验，请补充试验点。可选记录：{choices}")
    result = candidates.loc[candidates["trial_id"] == trial_ids[0]].copy()
    return result, {"trial_id": str(trial_ids[0]), "year": year, "site_name": str(result["site_name"].iloc[0])}


def _rcbd_check(data: pd.DataFrame) -> dict[str, Any]:
    blocks = sorted(int(value) for value in data["block_no"].dropna().unique())
    materials = sorted(str(value) for value in data["material_code"].dropna().unique())
    treatments = sorted(str(value) for value in data["treatment_code"].dropna().unique())
    dimensions = ["material_code", "treatment_code", "block_no"]
    duplicate_count = int(data.duplicated(dimensions).sum())
    expected_count = len(blocks) * len(materials) * len(treatments)
    issues = []
    if len(blocks) < 2:
        issues.append("有效区组少于 2 个")
    if len(materials) < 2:
        issues.append("材料数少于 2 个")
    if len(treatments) < 1:
        issues.append("未识别处理")
    if duplicate_count:
        issues.append(f"存在 {duplicate_count} 条重复的材料 × 处理 × 区组记录")
    if len(data) != expected_count:
        issues.append(f"组合不平衡：应为 {expected_count} 个小区记录，实际为 {len(data)} 个")
    return {
        "passed": not issues,
        "block_count": len(blocks),
        "material_count": len(materials),
        "treatment_count": len(treatments),
        "expected_entry_count": expected_count,
        "observed_entry_count": int(len(data)),
        "issues": issues,
    }


def _material_summary(data: pd.DataFrame, traits: Iterable[str]) -> list[dict[str, Any]]:
    aggregations: dict[str, list[str]] = {"yield_per_mu": ["mean", "std", "count"]}
    for trait in traits:
        if trait in data.columns and trait != "yield_per_mu":
            aggregations[trait] = ["mean"]
    grouped = data.groupby(["material_code", "material_name", "is_check"], dropna=False).agg(aggregations)
    grouped.columns = ["_".join(item).rstrip("_") for item in grouped.columns]
    grouped = grouped.reset_index()
    output = []
    for _, row in grouped.iterrows():
        record = {
            "material_code": row["material_code"],
            "material_name": row["material_name"],
            "is_check": bool(row["is_check"]),
            "mean_yield_kg_per_mu": _round(row.get("yield_per_mu_mean"), 2),
            "yield_sd": _round(row.get("yield_per_mu_std"), 2),
            "plot_count": int(row.get("yield_per_mu_count") or 0),
        }
        for trait in traits:
            if trait != "yield_per_mu" and f"{trait}_mean" in row:
                record[f"mean_{trait}"] = _round(row[f"{trait}_mean"], 2)
        output.append(record)
    return sorted(output, key=lambda item: item["mean_yield_kg_per_mu"] or -float("inf"), reverse=True)


def _run_same_trial_anova(data: pd.DataFrame, question: str) -> dict[str, Any]:
    trial, scope = _select_single_trial(data, question)
    treatment = _question_treatment(question)
    subset = trial.loc[trial["treatment_code"] == treatment].dropna(subset=["yield_per_mu"]).copy()
    if subset.empty:
        raise TrialStatisticsError(f"{scope['year']} 年{scope['site_name']}没有 {treatment} 处理下的产量数据")
    check = _rcbd_check(subset.assign(treatment_code=STANDARD_TREATMENT))
    if not check["passed"]:
        raise TrialStatisticsError("同试验随机区组核验未通过：" + "；".join(check["issues"]))
    model_formula = "yield_per_mu ~ C(block_no) + C(material_code)"
    model = ols(model_formula, data=subset).fit()
    tukey_rows = []
    if subset["material_code"].nunique() > 2 and len(subset) > subset["material_code"].nunique():
        tukey = pairwise_tukeyhsd(subset["yield_per_mu"], subset["material_code"])
        for row in tukey.summary().data[1:]:
            tukey_rows.append({
                "group_a": str(row[0]), "group_b": str(row[1]), "mean_difference": _round(row[2], 3),
                "p_adjusted": _round(row[3], 6), "lower": _round(row[4], 3), "upper": _round(row[5], 3),
                "significant": bool(row[6]),
            })
    return {
        "analysis_type": "rcbd_same_trial_anova",
        "title": "同试验材料比较（随机区组方差分析）",
        "filters": {**scope, "treatment_code": treatment},
        "model_formula": model_formula,
        "design_check": check,
        "anova": _anova_rows(model),
        "material_means": _material_summary(subset, ("plant_height", "thousand_grain_weight", "lodging_score")),
        "multiple_comparison": {"method": "Tukey HSD", "comparisons": tukey_rows},
        "limitations": "该模型只比较同一年、同一试验点、同一处理下的材料差异；不能将显著性外推到其他地点、年份或管理条件。",
        "source_trial_ids": [scope["trial_id"]],
        "source_record_count": int(len(subset)),
    }


def _run_factorial_rcbd(data: pd.DataFrame, question: str) -> dict[str, Any]:
    site = _find_site(data, question)
    requested_year = _question_year(question)
    if site:
        trial, scope = _select_single_trial(data, question)
        subset = trial.dropna(subset=["yield_per_mu"]).copy()
        check = _rcbd_check(subset)
        if not check["passed"]:
            raise TrialStatisticsError("材料 × 施氮随机区组核验未通过：" + "；".join(check["issues"]))
        model_formula = "yield_per_mu ~ C(block_no) + C(material_code) * C(treatment_code)"
        source_trial_ids = [scope["trial_id"]]
        scope["analysis_scope"] = "单一试验环境"
    else:
        subset = data.dropna(subset=["yield_per_mu"]).copy()
        if requested_year:
            subset = subset.loc[subset["trial_year"] == requested_year].copy()
        if subset.empty:
            raise TrialStatisticsError("已发布资料中没有满足年份条件的材料 × 施氮观测")
        checks = [_rcbd_check(group) for _, group in subset.groupby("trial_id")]
        failed_checks = [check for check in checks if not check["passed"]]
        if failed_checks:
            raise TrialStatisticsError("跨环境材料 × 施氮分析中存在未通过随机区组核验的试验环境")
        subset["environment_id"] = subset["trial_code"]
        scope = {
            "analysis_scope": "跨已发布试验环境",
            "year": requested_year,
            "environment_count": int(subset["trial_id"].nunique()),
            "treatment_codes": sorted(str(value) for value in subset["treatment_code"].unique()),
        }
        check = {"passed": True, "environment_check_count": len(checks), "mode": "each_environment_rcbd"}
        model_formula = "yield_per_mu ~ C(environment_id) + C(environment_id):C(block_no) + C(material_code) * C(treatment_code)"
        source_trial_ids = sorted(str(item) for item in subset["trial_id"].unique())
    if subset["treatment_code"].nunique() < 2:
        raise TrialStatisticsError("该试验只有一个处理，不能进行材料 × 施氮交互分析")
    model = ols(model_formula, data=subset).fit()
    material_effects = []
    for material_code, group in subset.groupby("material_code"):
        standard = group.loc[group["treatment_code"] == STANDARD_TREATMENT]
        high = group.loc[group["treatment_code"] == HIGH_NITROGEN_TREATMENT]
        if standard.empty or high.empty:
            continue
        material_effects.append({
            "material_code": material_code,
            "material_name": str(group["material_name"].iloc[0]),
            "standard_n_mean_yield": _round(standard["yield_per_mu"].mean(), 2),
            "high_n_mean_yield": _round(high["yield_per_mu"].mean(), 2),
            "yield_change_kg_per_mu": _round(high["yield_per_mu"].mean() - standard["yield_per_mu"].mean(), 2),
            "lodging_change": _round(high["lodging_score"].mean() - standard["lodging_score"].mean(), 2),
        })
    return {
        "analysis_type": "factorial_rcbd_management",
        "title": "管理措施影响（材料 × 施氮随机区组方差分析）",
        "filters": scope,
        "model_formula": model_formula,
        "design_check": check,
        "anova": _anova_rows(model),
        "material_treatment_effects": sorted(material_effects, key=lambda item: item["yield_change_kg_per_mu"] or -float("inf"), reverse=True),
        "limitations": "施氮以外未记录或未控制的管理差异仍可能影响观测结果；跨环境分析将环境和环境内区组作为固定效应控制，不等同于混合效应模型或长期因果结论。",
        "source_trial_ids": source_trial_ids,
        "source_record_count": int(len(subset)),
    }


def _run_stability(data: pd.DataFrame, question: str) -> dict[str, Any]:
    treatment = _question_treatment(question)
    subset = data.loc[(data["treatment_code"] == treatment) & data["yield_per_mu"].notna()].copy()
    if subset["trial_id"].nunique() < 3:
        raise TrialStatisticsError("有效环境少于 3 个，不能完成多年多点稳定性分析")
    environment_means = subset.groupby(["trial_id", "trial_code", "trial_year", "site_name", "material_code", "material_name", "is_check"], as_index=False)["yield_per_mu"].mean()
    environment_means["environment_id"] = environment_means["trial_code"]
    subset["environment_id"] = subset["trial_code"]
    model_formula = "yield_per_mu ~ C(material_code) + C(environment_id) + C(material_code):C(environment_id) + C(environment_id):C(block_no)"
    model = ols(model_formula, data=subset).fit()
    records = _material_summary(environment_means, ())
    checks = environment_means.loc[environment_means["is_check"]].groupby("environment_id")["yield_per_mu"].mean().to_dict()
    for record in records:
        rows = environment_means.loc[environment_means["material_code"] == record["material_code"]]
        relative = []
        for _, row in rows.iterrows():
            check_yield = checks.get(row["environment_id"])
            if check_yield:
                relative.append((row["yield_per_mu"] / check_yield - 1) * 100)
        record["environment_count"] = int(rows["environment_id"].nunique())
        record["yield_cv_percent"] = _round((rows["yield_per_mu"].std() / rows["yield_per_mu"].mean() * 100) if rows["yield_per_mu"].mean() else None, 2)
        record["relative_yield_to_checks_percent"] = _round(pd.Series(relative).mean() if relative else None, 2)
    return {
        "analysis_type": "multi_environment_stability",
        "title": "多年多点稳定性与环境互作",
        "filters": {"treatment_code": treatment, "environment_count": int(environment_means["environment_id"].nunique())},
        "model_formula": model_formula,
        "anova": _anova_rows(model),
        "material_stability": records,
        "limitations": "该版本给出材料、环境及材料 × 环境互作的固定效应方差分析，以及均值、变异系数和相对对照表现；AMMI/GGE 双标图和混合模型将在后续版本引入。",
        "source_trial_ids": sorted(str(item) for item in environment_means["trial_id"].unique()),
        "source_record_count": int(len(subset)),
    }


def _run_environment_association(data: pd.DataFrame, environment: pd.DataFrame) -> dict[str, Any]:
    subset = data.loc[(data["treatment_code"] == STANDARD_TREATMENT) & data["yield_per_mu"].notna()].copy()
    means = subset.groupby("trial_id", as_index=False).agg(
        mean_yield=("yield_per_mu", "mean"),
        mean_seed_setting_rate=("seed_setting_rate", "mean"),
        mean_thousand_grain_weight=("thousand_grain_weight", "mean"),
    )
    merged = means.merge(environment, on="trial_id", how="inner")
    factor_columns = ["soil_ph", "available_phosphorus", "rainfall"]
    usable = merged.dropna(subset=factor_columns)
    if len(usable) < 6:
        raise TrialStatisticsError("环境指标完整的试验环境少于 6 个，无法进行多因素环境关联模型")
    models = []
    for outcome in ("mean_yield", "mean_seed_setting_rate", "mean_thousand_grain_weight"):
        outcome_data = usable.dropna(subset=[outcome]).copy()
        formula = f"{outcome} ~ soil_ph + available_phosphorus + rainfall"
        if outcome_data[outcome].nunique() < 2:
            models.append({
                "outcome": outcome,
                "formula": formula,
                "status": "unavailable",
                "sample_size": int(len(outcome_data)),
                "reason": "当前资料包中该性状在有效环境之间没有可用于回归的数值变异。",
            })
            continue
        fitted = ols(formula, data=outcome_data).fit()
        r_squared = _round(fitted.rsquared, 4)
        models.append({
            "outcome": outcome,
            "formula": formula,
            "status": "completed" if r_squared is not None else "unavailable",
            "r_squared": r_squared,
            "adjusted_r_squared": _round(fitted.rsquared_adj, 4),
            "sample_size": int(fitted.nobs),
            "coefficients": [
                {"factor": str(name), "coefficient": _round(value, 5), "p_value": _round(fitted.pvalues.get(name), 6)}
                for name, value in fitted.params.items()
            ],
        })
    return {
        "analysis_type": "environment_association_regression",
        "title": "土壤与环境影响关联",
        "filters": {"treatment_code": STANDARD_TREATMENT, "environment_count": int(len(usable))},
        "model_formula": "结果分别拟合：性状均值 ~ 土壤 pH + 有效磷 + 降雨量",
        "environment_models": models,
        "limitations": "环境数通常较少，且地点、年份、管理及材料组成可能共同变化。本模型仅量化当前资料包内的关联，不证明任何单一环境因素造成性状变化。",
        "source_trial_ids": sorted(str(item) for item in usable["trial_id"].unique()),
        "source_record_count": int(len(subset)),
    }


def _run_tradeoff(data: pd.DataFrame) -> dict[str, Any]:
    subset = data.loc[data["treatment_code"] == STANDARD_TREATMENT].copy()
    means = subset.groupby(["material_code", "material_name", "is_check"], as_index=False).agg(
        mean_yield=("yield_per_mu", "mean"),
        mean_head_rice_rate=("head_rice_rate", "mean"),
        mean_chalkiness_degree=("chalkiness_degree", "mean"),
        mean_lodging_score=("lodging_score", "mean"),
    )
    correlations = []
    for field, label in (("mean_head_rice_rate", "整精米率"), ("mean_chalkiness_degree", "垩白度"), ("mean_lodging_score", "倒伏等级")):
        usable = means[["mean_yield", field]].dropna()
        correlations.append({"trait": label, "sample_size": int(len(usable)), "pearson_r": _round(usable["mean_yield"].corr(usable[field]), 4)})
    records = _native(means.sort_values("mean_yield", ascending=False).to_dict("records"))
    return {
        "analysis_type": "trait_tradeoff",
        "title": "产量、米质与倒伏性状权衡",
        "filters": {"treatment_code": STANDARD_TREATMENT},
        "model_formula": "材料跨环境均值的 Pearson 相关分析",
        "material_means": records,
        "correlations": correlations,
        "limitations": "相关性为当前材料集合在标准施氮下的描述，不等同于遗传连锁、因果机制或对未来组合的预测。",
        "source_trial_ids": sorted(str(item) for item in subset["trial_id"].unique()),
        "source_record_count": int(len(subset)),
    }


def _run_decline_evidence(data: pd.DataFrame, environment: pd.DataFrame, question: str) -> dict[str, Any]:
    normalized = _key(question)
    material_code = next((str(code) for code in data["material_code"].unique() if _key(code) in normalized), None)
    if material_code is None:
        for _, row in data[["material_code", "material_name"]].drop_duplicates().iterrows():
            if _key(row["material_name"]) in normalized:
                material_code = str(row["material_code"])
                break
    candidates = data.loc[~data["is_check"]]
    if material_code is None:
        by_year = candidates.loc[candidates["treatment_code"] == STANDARD_TREATMENT].groupby(["material_code", "trial_year"])["yield_per_mu"].mean().unstack()
        if 2025 in by_year.columns and len(by_year.columns) > 1:
            prior = by_year.drop(columns=[2025]).mean(axis=1)
            material_code = str((by_year[2025] - prior).idxmin())
        else:
            material_code = str(candidates["material_code"].iloc[0])
    subset = data.loc[(data["material_code"] == material_code) & (data["treatment_code"] == STANDARD_TREATMENT)].copy()
    if subset.empty:
        raise TrialStatisticsError("未找到该材料在标准施氮处理下的可追溯观测")
    grouped = subset.groupby(["trial_id", "trial_code", "trial_year", "site_name"], as_index=False).agg(
        mean_yield=("yield_per_mu", "mean"),
        mean_setting=("seed_setting_rate", "mean"),
        mean_grain_weight=("thousand_grain_weight", "mean"),
        mean_blast=("panicle_blast_score", "mean"),
        observation_count=("entry_id", "count"),
    ).merge(environment, on="trial_id", how="left")
    return {
        "analysis_type": "performance_decline_evidence",
        "title": "材料表现变差的证据拆解",
        "filters": {"material_code": material_code, "material_name": str(subset["material_name"].iloc[0]), "treatment_code": STANDARD_TREATMENT},
        "model_formula": "同一材料的年点观测、环境和病害压力并列比较（不做单因素因果归因）",
        "records": _native(grouped.sort_values(["trial_year", "site_name"]).to_dict("records")),
        "limitations": "平台可指出产量变化与同期土壤、天气、病害压力及结实率、千粒重变化是否同时出现；缺少随机对照或完整协变量时，不能判定某一因素是唯一原因。",
        "source_trial_ids": sorted(str(item) for item in grouped["trial_id"].unique()),
        "source_record_count": int(len(subset)),
    }


def _record_run(session: Session, package_id: str, analysis: dict[str, Any], question: str, requested_by: str) -> str:
    run_id = str(uuid.uuid4())
    try:
        session.execute(text("""
        INSERT INTO trial_analysis_run (
            id, package_id, analysis_type, analysis_version, requested_by, request_question,
            filters, model_formula, engine_name, source_record_count, source_trial_ids,
            status, result_json, limitation_note, completed_at
        ) VALUES (
            :id, :package_id, :analysis_type, :analysis_version, :requested_by, :question,
            CAST(:filters AS jsonb), :model_formula, :engine_name, :source_record_count, CAST(:source_trial_ids AS jsonb),
            'completed', CAST(:result_json AS jsonb), :limitation_note, now()
        )
        """), {
        "id": run_id,
        "package_id": package_id,
        "analysis_type": analysis["analysis_type"],
        "analysis_version": ANALYSIS_VERSION,
        "requested_by": requested_by,
        "question": question,
        "filters": _json(analysis.get("filters", {})),
        "model_formula": analysis.get("model_formula"),
        "engine_name": ANALYSIS_ENGINE_NAME,
        "source_record_count": analysis.get("source_record_count"),
        "source_trial_ids": _json(analysis.get("source_trial_ids", [])),
        "result_json": _json(_native(analysis)),
        "limitation_note": analysis.get("limitations", "请结合试验设计和数据质量解释结果。"),
        })
        # This function runs inside the research request transaction.  Do not
        # commit here: the request must commit the analysis run together with
        # the question and its audit event under the same RLS identity.
        session.flush()
    except Exception:
        session.rollback()
        raise
    return run_id


def run_controlled_trial_analysis(session: Session, package: dict[str, Any], question: str, requested_by: str) -> dict[str, Any] | None:
    """Choose one reviewed procedure, persist it, and return a JSON-safe result."""
    normalized = _key(question)
    triggers = {
        "same_trial": ("同一试验", "同一环境", "哪些材料", "株高更低", "材料比较", "单因素", "多重比较", "tukey", "与对照", "高于对照", "差异是否显著"),
        "stability": ("稳定性", "多年多点", "相对增产", "有效环境", "高产稳产", "候选材料"),
        "environment": ("土壤ph", "有效磷", "降雨", "环境影响", "环境关联", "环境敏感"),
        "management": ("管理措施", "高氮", "较高施氮", "不同施氮", "施氮交互", "材料施氮交互", "倒伏风险"),
        "tradeoff": ("权衡", "取舍", "米质", "高产材料"),
        "decline": ("下降", "变差", "异常", "证据拆解"),
    }
    decline_words = triggers["decline"] + ("产量变化", "病害压力", "原因")
    cross_treatment_intent = any(word in normalized for word in (
        "高氮", "较高施氮", "不同施氮", "标准施氮和较高施氮", "标准施氮与较高施氮",
        "施氮交互", "材料施氮交互", "交互效应", "m2",
    ))
    single_treatment_intent = any(word in normalized for word in ("标准施氮", "标准氮", "m1")) and not cross_treatment_intent
    stability_intent = any(word in normalized for word in triggers["stability"])
    # "标准施氮 + 材料" is also a normal condition in a multi-environment
    # stability question.  Do not let that generic wording force the
    # same-trial model, which requires one uniquely identified site.
    # These phrases really require one experimental environment.  Broader
    # wording such as "材料" or "与对照" is deliberately excluded because it
    # is equally common in multi-year, multi-site questions.
    explicit_same_trial_intent = any(word in normalized for word in (
        "同一试验", "同一环境", "单因素", "Tukey", "多重比较", "差异是否显著", "显著性检验",
    ))
    same_trial_intent = explicit_same_trial_intent or (
        single_treatment_intent
        and any(word in normalized for word in ("方差分析", "全部材料", "Tukey", "多重比较", "显著"))
        and not stability_intent
    )
    management_intent = any(word in normalized for word in triggers["management"]) or cross_treatment_intent
    if any(word in normalized for word in decline_words):
        analysis_key = "decline"
    elif stability_intent and not explicit_same_trial_intent:
        analysis_key = "stability"
    elif same_trial_intent and not management_intent:
        analysis_key = "same_trial"
    else:
        priority = ("environment", "stability", "tradeoff", "management", "same_trial")
        analysis_key = next(
            (key for key in priority if any(word in normalized for word in triggers[key])),
            None,
        )
    if analysis_key is None:
        return None
    try:
        data = _frame(session, str(package["id"]))
        environment = _environment_frame(session, str(package["id"]))
        if analysis_key == "same_trial":
            analysis = _run_same_trial_anova(data, question)
        elif analysis_key == "stability":
            analysis = _run_stability(data, question)
        elif analysis_key == "environment":
            analysis = _run_environment_association(data, environment)
        elif analysis_key == "management":
            analysis = _run_factorial_rcbd(data, question)
        elif analysis_key == "tradeoff":
            analysis = _run_tradeoff(data)
        else:
            analysis = _run_decline_evidence(data, environment, question)
    except TrialStatisticsError:
        raise
    except Exception as exc:
        raise TrialStatisticsError(
            "本地统计引擎未能完成该分析。请确认已发布区域试验资料包完整，"
            "并检查试验设计、区组、材料、处理及观测值是否满足统计条件。"
        ) from exc
    run_id = _record_run(session, str(package["id"]), analysis, question, requested_by)
    return {**_native(analysis), "analysis_run_id": run_id, "engine": ANALYSIS_ENGINE_NAME, "analysis_version": ANALYSIS_VERSION}
