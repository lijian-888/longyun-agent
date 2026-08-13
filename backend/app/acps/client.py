"""Outbound ADP discovery and AIP invocation used by Longyun as a Leader."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from acps_sdk.adp import DiscoveryRequest, DiscoveryResponse
from acps_sdk.aip import (
    StructuredDataItem,
    TaskCommand,
    TaskCommandType,
    TaskResult,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_model import RpcRequest, RpcRequestParams, RpcResponse


def build_mtls_context(
    *,
    ca_file: str,
    certificate_file: str,
    private_key_file: str,
) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate_file, private_key_file)
    return context


@dataclass(frozen=True)
class DiscoveredPartner:
    aic: str
    skill_id: str
    rpc_url: str
    acs: dict[str, Any]


class AcpsDiscoveryClient:
    def __init__(
        self,
        discovery_base_url: str,
        *,
        ssl_context: ssl.SSLContext,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.discovery_url = discovery_base_url.rstrip("/") + "/discover"
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.AsyncClient(
            verify=ssl_context,
            timeout=30.0,
        )

    async def discover(
        self,
        query: str,
        *,
        skill_id: str | None = None,
        limit: int = 5,
    ) -> list[DiscoveredPartner]:
        request = DiscoveryRequest(type="explicit", query=query, limit=limit)
        response = await self.http_client.post(
            self.discovery_url,
            json=request.to_dict(),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        parsed = DiscoveryResponse.model_validate(response.json())
        if parsed.error:
            raise RuntimeError(
                f"Discovery error {parsed.error.code}: {parsed.error.message}"
            )
        if not parsed.result:
            return []
        partners: list[DiscoveredPartner] = []
        for aic, acs, skill, _group in parsed.result.iter_agent_skills():
            if skill_id and skill.skill_id != skill_id:
                continue
            rpc_url = next((
                str(endpoint.get("url") or "")
                for endpoint in acs.get("endPoints", [])
                if str(endpoint.get("transport") or "").upper() == "JSONRPC"
            ), "")
            if rpc_url:
                partners.append(DiscoveredPartner(
                    aic=aic,
                    skill_id=skill.skill_id,
                    rpc_url=rpc_url,
                    acs=acs,
                ))
        return partners

    async def close(self) -> None:
        if self._owns_client:
            await self.http_client.aclose()


class AipPartnerClient:
    """Send native TaskCommand objects; no Longyun-specific payload is leaked."""

    def __init__(
        self,
        leader_aic: str,
        *,
        ssl_context: ssl.SSLContext,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.leader_aic = leader_aic
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.AsyncClient(
            verify=ssl_context,
            timeout=30.0,
        )

    async def start(
        self,
        partner: DiscoveredPartner,
        *,
        session_id: str,
        text: str,
        task_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> TaskResult:
        command_params: dict[str, Any] = {"skillIds": [partner.skill_id]}
        if timeout_ms is not None:
            command_params["timeout"] = timeout_ms
        command = self._command(
            TaskCommandType.Start,
            task_id=task_id or f"task-{uuid4()}",
            session_id=session_id,
            data_items=[
                TextDataItem(text=text),
                StructuredDataItem(data={"skillIds": [partner.skill_id]}),
            ],
            command_params=command_params,
        )
        return await self.send(partner.rpc_url, command)

    async def get(
        self,
        partner_url: str,
        *,
        task_id: str,
        session_id: str,
    ) -> TaskResult:
        return await self.send(
            partner_url,
            self._command(TaskCommandType.Get, task_id=task_id, session_id=session_id),
        )

    async def cancel(
        self,
        partner_url: str,
        *,
        task_id: str,
        session_id: str,
    ) -> TaskResult:
        return await self.send(
            partner_url,
            self._command(TaskCommandType.Cancel, task_id=task_id, session_id=session_id),
        )

    async def complete(
        self,
        partner_url: str,
        *,
        task_id: str,
        session_id: str,
    ) -> TaskResult:
        return await self.send(
            partner_url,
            self._command(TaskCommandType.Complete, task_id=task_id, session_id=session_id),
        )

    async def send(self, partner_url: str, command: TaskCommand) -> TaskResult:
        request = RpcRequest(
            id=str(uuid4()),
            params=RpcRequestParams(command=command),
        )
        response = await self.http_client.post(
            partner_url,
            json=request.model_dump(mode="json", exclude_none=True),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        rpc = RpcResponse.model_validate(response.json())
        if rpc.id != request.id:
            raise RuntimeError("AIP response id does not match request id")
        if rpc.error:
            raise RuntimeError(f"AIP error {rpc.error.code}: {rpc.error.message}")
        if not rpc.result:
            raise RuntimeError("AIP response did not contain TaskResult")
        return rpc.result

    def _command(
        self,
        command_type: TaskCommandType,
        *,
        task_id: str,
        session_id: str,
        data_items: list[Any] | None = None,
        command_params: dict[str, Any] | None = None,
    ) -> TaskCommand:
        return TaskCommand(
            id=f"command-{uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader",
            senderId=self.leader_aic,
            sessionId=session_id,
            taskId=task_id,
            command=command_type,
            commandParams=command_params,
            dataItems=data_items,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.http_client.aclose()


class LongyunLeaderGateway:
    """Discovery -> AIP delegation path for future external specialists."""

    def __init__(
        self,
        discovery: AcpsDiscoveryClient,
        aip: AipPartnerClient,
    ) -> None:
        self.discovery = discovery
        self.aip = aip

    async def delegate(
        self,
        *,
        query: str,
        task_text: str,
        session_id: str,
        skill_id: str | None = None,
    ) -> TaskResult:
        partners = await self.discovery.discover(query, skill_id=skill_id)
        if not partners:
            raise LookupError("Discovery did not return an AIP-compatible partner")
        return await self.aip.start(
            partners[0],
            session_id=session_id,
            text=task_text,
        )
