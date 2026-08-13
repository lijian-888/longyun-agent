"""FastAPI JSON-RPC boundary for AIP v2.1 task commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from acps_sdk.aip.aip_rpc_model import JSONRPCError, RpcRequest, RpcResponse
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .config import AcpsSettings
from .service import AipApplicationError, LongyunAipService


@dataclass(frozen=True)
class AcpsApiDependencies:
    get_session: Callable[..., Session]
    set_research_owner: Callable[..., None]


def build_acps_router(
    dependencies: AcpsApiDependencies,
    settings: AcpsSettings,
    *,
    service: LongyunAipService | None = None,
) -> APIRouter:
    router = APIRouter(tags=["ACPs AIP"])
    application = service or LongyunAipService(settings)

    @router.post(settings.rpc_path, response_model=RpcResponse)
    def aip_rpc(
        payload: dict[str, Any],
        session: Session = Depends(dependencies.get_session),
        client_aic: str | None = Header(default=None, alias="X-ACPS-Client-AIC"),
        mtls_verified: str | None = Header(
            default=None,
            alias="X-ACPS-mTLS-Verified",
        ),
    ) -> RpcResponse:
        if not settings.enabled:
            raise HTTPException(503, "AIP boundary is disabled")
        if settings.require_mtls_proxy and (mtls_verified or "").upper() != "SUCCESS":
            raise HTTPException(401, "A verified mTLS client certificate is required")
        peer_aic = (client_aic or "").strip()
        if not peer_aic:
            raise HTTPException(401, "Verified client AIC is missing")
        identity = settings.binding_for(peer_aic)
        if not identity:
            raise HTTPException(403, "Client AIC is not authorized")

        request_id = payload.get("id") if isinstance(payload, dict) else None
        try:
            request = RpcRequest.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            return RpcResponse(
                id=request_id,
                error=JSONRPCError(code=-32602, message="Invalid request", data=str(exc)),
            )
        command = request.params.command
        if command.senderRole != "leader" or command.senderId != peer_aic:
            raise HTTPException(
                403,
                "TaskCommand senderId must match the verified mTLS client AIC",
            )
        dependencies.set_research_owner(
            session,
            identity.owner_id,
            identity.institution_id,
            institution_admin=False,
        )
        try:
            result = application.handle(
                session,
                leader_aic=peer_aic,
                binding=identity,
                command=command,
            )
            return RpcResponse(id=request.id, result=result)
        except AipApplicationError as exc:
            return RpcResponse(
                id=request.id,
                error=JSONRPCError(
                    code=exc.code,
                    message=exc.message,
                    data=exc.data,
                ),
            )

    return router
