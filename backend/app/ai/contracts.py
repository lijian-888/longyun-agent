"""Typed contracts shared by Longyun orchestration, tools and persistence.

The language model may explain a result, but it is not the source of record for
tool execution, evidence provenance or workflow state.  Those values are
validated here before they cross an agent boundary.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=240)
    title: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=120)
    locator: str = Field(default="", max_length=1000)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(default_factory=lambda: str(uuid4()))
    tool_code: str = Field(min_length=1, max_length=100)
    status: Literal["completed", "not_applicable", "unavailable", "failed"]
    data_classification: Literal[
        "public", "desensitized", "institution_private"
    ] = "institution_private"
    summary: str = Field(default="", max_length=4000)
    data: dict[str, Any] | list[Any] | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    error_code: str | None = Field(default=None, max_length=100)
    started_at: str = Field(default_factory=utc_now_iso)
    completed_at: str = Field(default_factory=utc_now_iso)


class AgentStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusions: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=30)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    missing_data: list[str] = Field(default_factory=list, max_length=30)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)
    next_steps: list[str] = Field(default_factory=list, max_length=30)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator(
        "conclusions",
        "recommendations",
        "evidence_ids",
        "missing_data",
        "uncertainties",
        "next_steps",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            normalized = str(item or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result


class AgentArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_code: str
    agent_name: str
    agent_version: str
    contract_version: str
    content: str
    structured_output: AgentStructuredOutput
    tool_results: list[ToolResult] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    model_alias: str = ""
    created_at: str = Field(default_factory=utc_now_iso)


class WorkflowPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_code: str
    agent_version: str
    contract_version: str
    dependencies: list[str] = Field(default_factory=list)
    tool_codes: list[str] = Field(default_factory=list)


def json_schema_instruction() -> str:
    """Return the stable model-facing schema without Python implementation details."""
    return json.dumps(
        {
            "conclusions": ["由证据支持的结论"],
            "recommendations": ["辅助建议"],
            "evidence_ids": ["本次工具结果实际提供的证据编号"],
            "missing_data": ["完成更强结论仍缺少的数据"],
            "uncertainties": ["不确定性和适用边界"],
            "next_steps": ["下一步验证动作"],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )


def parse_structured_output(raw_content: str, allowed_evidence_ids: set[str]) -> AgentStructuredOutput:
    """Parse a JSON answer and fail safely to a typed, low-confidence result.

    Compatible model gateways sometimes wrap JSON in Markdown or return plain
    text.  The fallback preserves the answer for the user while refusing to
    invent citations or confidence.
    """
    content = (raw_content or "").strip()
    candidate = content
    if "```" in candidate:
        blocks = candidate.split("```")
        if len(blocks) >= 3:
            candidate = blocks[1]
            if candidate.lstrip().lower().startswith("json"):
                candidate = candidate.lstrip()[4:].lstrip()
    try:
        payload = json.loads(candidate)
        parsed = AgentStructuredOutput.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = AgentStructuredOutput(
            conclusions=[content] if content else [],
            uncertainties=["模型未返回结构化结果，本次内容仅作为待复核解释。"],
            confidence=None,
        )
    parsed.evidence_ids = [
        evidence_id
        for evidence_id in parsed.evidence_ids
        if evidence_id in allowed_evidence_ids
    ]
    return parsed


def render_structured_output(output: AgentStructuredOutput) -> str:
    sections = (
        ("结论", output.conclusions),
        ("建议", output.recommendations),
        ("证据编号", output.evidence_ids),
        ("缺失数据", output.missing_data),
        ("不确定性与边界", output.uncertainties),
        ("下一步验证", output.next_steps),
    )
    lines: list[str] = []
    for heading, values in sections:
        if not values:
            continue
        lines.append(f"### {heading}")
        lines.extend(f"- {value}" for value in values)
        lines.append("")
    if output.confidence is not None:
        lines.extend(("### 置信度", f"- {output.confidence:.2f}", ""))
    return "\n".join(lines).strip()
