"""Versioned definitions and deterministic routing for Longyun sub-agents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class AgentSpec:
    code: str
    name: str
    description: str
    version: str
    capabilities: tuple[str, ...]
    tool_codes: tuple[str, ...]
    dependencies: tuple[str, ...]
    required_scopes: tuple[str, ...]
    contract_version: str
    keywords: tuple[str, ...]
    system_prompt: str


AGENT_SPECS: dict[str, AgentSpec] = {
    "germplasm_analysis": AgentSpec(
        code="germplasm_analysis",
        name="种质鉴析智能体",
        description="种质档案、表型与基因型证据、GWAS/QTL结果和候选基因解释。",
        version="1.0.0",
        capabilities=("种质资源解析", "性状关联", "基因型质控解释", "候选基因证据整理"),
        tool_codes=("query_germplasm", "read_genotype_qc", "read_gwas_result", "search_gene_evidence"),
        dependencies=(),
        required_scopes=("institution", "project_optional", "owner_private_evidence"),
        contract_version="1.0.0",
        keywords=("种质", "材料", "基因", "基因型", "gwas", "qtl", "位点", "等位", "性状关联", "候选基因", "vcf", "plink"),
        system_prompt=(
            "你是隆耘种质鉴析智能体。只依据授权的机构数据、受控算法结果和带来源证据进行分析。"
            "不得把语言模型推断表述为基因发现，不得编造位点、材料、性状或统计值。"
            "输出必须区分事实、解释、缺失数据和不确定性。"
        ),
    ),
    "parent_combination": AgentSpec(
        code="parent_combination",
        name="亲本配组智能体",
        description="依据目标性状、关键等位基因、亲缘、花期和生态区进行辅助推荐。",
        version="1.0.0",
        capabilities=("约束筛选", "辅助排序", "配组理由", "验证计划"),
        tool_codes=("query_parent_candidates", "score_parent_constraints", "read_kinship", "build_validation_plan"),
        dependencies=("germplasm_analysis",),
        required_scopes=("institution", "project_optional", "owner_private_evidence"),
        contract_version="1.0.0",
        keywords=("亲本", "配组", "杂交", "组合", "父本", "母本", "花期", "亲缘", "优势筛选"),
        system_prompt=(
            "你是隆耘亲本配组智能体。当前产品能力是辅助推荐而不是经过育种验证的产量预测。"
            "必须说明使用了哪些材料字段、约束、规则和证据；缺少亲缘、花期或环境数据时必须明确提示。"
            "不得宣称虚拟杂交结果等同于真实田间表现。"
        ),
    ),
    "trial_analysis": AgentSpec(
        code="trial_analysis",
        name="试验分析智能体",
        description="试验设计检查、表型统计、多年多点分析、稳定性解释和复盘建议。",
        version="1.0.0",
        capabilities=("试验设计检查", "表型分析", "区域试验统计", "复盘迭代"),
        tool_codes=("validate_trial_package", "run_trial_statistics", "read_trial_result", "generate_trial_report"),
        dependencies=(),
        required_scopes=("institution", "project_optional", "owner_private_evidence"),
        contract_version="1.0.0",
        keywords=("试验", "田间", "表型", "区试", "多年多点", "方差", "tukey", "稳定性", "重复", "环境", "产量", "处理"),
        system_prompt=(
            "你是隆耘试验分析智能体。统计数值只能引用受控程序产生的结果，不得自行口算或编造。"
            "回答需要说明试验设计、样本范围、方法版本、显著性和适用边界，并给出下一轮验证建议。"
        ),
    ),
    "research_intelligence": AgentSpec(
        code="research_intelligence",
        name="科研情报智能体",
        description="文献调研、行业情报、证据矩阵、课题资料和知识沉淀。",
        version="1.0.0",
        capabilities=("文献调研", "情报挖掘", "证据整理", "成果沉淀"),
        tool_codes=("search_private_knowledge", "search_public_knowledge", "read_document_evidence", "build_evidence_matrix"),
        dependencies=(),
        required_scopes=("institution", "project_optional", "owner_private_evidence"),
        contract_version="1.0.0",
        keywords=("文献", "论文", "研究进展", "综述", "情报", "政策", "标准", "规程", "证据", "课题", "申报", "成果"),
        system_prompt=(
            "你是隆耘科研情报智能体。所有关键结论必须关联可追溯来源；无法核实的内容必须标记为待核实。"
            "不得虚构论文、作者、期刊、标准号、链接或发布日期。"
        ),
    ),
}


def get_agent_spec(code: str) -> AgentSpec:
    try:
        return AGENT_SPECS[code]
    except KeyError as exc:
        raise ValueError(f"未知的子智能体：{code}") from exc


def public_agent_catalog() -> list[dict[str, object]]:
    return [
        {
            "code": spec.code,
            "name": spec.name,
            "description": spec.description,
            "version": spec.version,
            "contract_version": spec.contract_version,
            "capabilities": list(spec.capabilities),
            "dependencies": list(spec.dependencies),
            "status": "available",
        }
        for spec in AGENT_SPECS.values()
    ]


def _normalized_codes(codes: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for code in codes or ():
        normalized = str(code).strip()
        get_agent_spec(normalized)
        if normalized not in result:
            result.append(normalized)
    return result


def route_question(question: str, requested_agents: Iterable[str] | None = None) -> list[str]:
    """Return a bounded, ordered plan; explicit user selection wins.

    Routing is deliberately deterministic.  An LLM may later propose a route,
    but the backend must still validate it against this registry and budget.
    """
    explicit = _normalized_codes(requested_agents)
    if explicit:
        selected = explicit
    else:
        lowered = question.casefold()
        scored: list[tuple[int, str]] = []
        for code, spec in AGENT_SPECS.items():
            score = sum(1 for keyword in spec.keywords if keyword.casefold() in lowered)
            if score:
                scored.append((score, code))
        scored.sort(key=lambda item: (-item[0], list(AGENT_SPECS).index(item[1])))
        selected = [code for _score, code in scored]
        if not selected:
            selected = ["research_intelligence"]

    # Dependencies are server-side safety contracts.  Explicit UI selection
    # cannot bypass the evidence preparation required by a downstream agent.
    pending = list(selected)
    while pending:
        code = pending.pop()
        for dependency in get_agent_spec(code).dependencies:
            if dependency not in selected:
                selected.append(dependency)
                pending.append(dependency)

    # Keep execution order stable for dependency-aware graph edges.
    order = {
        "germplasm_analysis": 0,
        "research_intelligence": 1,
        "parent_combination": 2,
        "trial_analysis": 3,
    }
    selected = sorted(set(selected), key=order.__getitem__)
    return selected[:4]


def question_mentions_sensitive_action(question: str) -> bool:
    return bool(re.search(r"(?:导出|发布|共享|删除|覆盖|写入|对外|提交)", question, re.IGNORECASE))
