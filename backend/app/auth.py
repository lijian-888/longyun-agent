"""Keycloak JWT verification and role gates for platform APIs."""

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .tenancy import TenantAccessError, tenant_database_manager


bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: dict[str, Any] = {"expires_at": 0.0, "keys": {}}


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated Keycloak identity used for all private resources."""

    id: str
    username: str
    display_name: str
    roles: frozenset[str]
    institution_id: str = "longyun-demo"


def _settings() -> tuple[str, str, str]:
    issuer = os.getenv("KEYCLOAK_ISSUER", "https://localhost:8443/realms/rice-research").rstrip("/")
    jwks_url = os.getenv("KEYCLOAK_JWKS_URL", f"{issuer}/protocol/openid-connect/certs")
    audience = os.getenv("KEYCLOAK_AUDIENCE", "rice-research-web")
    return issuer, jwks_url, audience


async def _jwks(jwks_url: str) -> dict[str, Any]:
    now = time.time()
    if _jwks_cache["keys"] and now < _jwks_cache["expires_at"]:
        return _jwks_cache["keys"]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # pragma: no cover - depends on the running identity service
        raise HTTPException(503, "身份认证服务暂不可用，请稍后再试。") from exc
    keys = {item["kid"]: item for item in payload.get("keys", []) if item.get("kid")}
    _jwks_cache.update({"expires_at": now + 300, "keys": keys})
    return keys


async def get_current_user(credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme)) -> CurrentUser:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "请先登录科研人员账号。")
    issuer, jwks_url, audience = _settings()
    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        key = (await _jwks(jwks_url)).get(header.get("kid"))
        if not key:
            _jwks_cache.update({"expires_at": 0.0, "keys": {}})
            key = (await _jwks(jwks_url)).get(header.get("kid"))
        if not key:
            raise ValueError("unknown signing key")
        claims = jwt.decode(
            token,
            jwt.algorithms.RSAAlgorithm.from_jwk(key),
            algorithms=[header.get("alg", "RS256")],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except Exception as exc:
        raise HTTPException(401, "登录凭证无效或已过期，请重新登录。") from exc

    token_audiences = claims.get("aud", [])
    if isinstance(token_audiences, str):
        token_audiences = [token_audiences]
    if audience not in token_audiences and claims.get("azp") != audience:
        raise HTTPException(401, "登录凭证不属于本科研助手。")
    roles = frozenset((claims.get("realm_access") or {}).get("roles") or [])
    institution_id = str(claims.get("institution_id") or "").strip()
    deployment_profile = os.getenv("DEPLOYMENT_ENV", "development").strip().lower()
    if not institution_id:
        if deployment_profile in {"production", "staging"}:
            raise HTTPException(403, "账号尚未绑定机构，禁止访问正式科研数据。")
        institution_id = os.getenv("DEFAULT_INSTITUTION_ID", "longyun-demo").strip()
    display_name = " ".join(part for part in [claims.get("family_name"), claims.get("given_name")] if part).strip()
    current_user = CurrentUser(
        id=str(claims.get("sub") or ""),
        username=str(claims.get("preferred_username") or ""),
        display_name=display_name or str(claims.get("preferred_username") or "科研人员"),
        roles=roles,
        institution_id=institution_id,
    )
    try:
        tenant_database_manager.verify_user_membership(
            current_user.id,
            current_user.institution_id,
        )
        if tenant_database_manager.mode == "multi":
            tenant_database_manager.resolve(current_user.institution_id)
    except TenantAccessError as exc:
        # Checking the mutable control plane on every authenticated request
        # makes a disabled/deleted account ineffective immediately, even when
        # its previously issued Keycloak access token has not expired yet.
        raise HTTPException(403, "当前账号已被停用、删除或所属机构不可用。") from exc
    return current_user


async def require_researcher(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    if "researcher" not in user.roles:
        raise HTTPException(403, "当前账号没有科研助手访问权限。")
    return user


async def require_published_data_reader(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    """Allow every platform role to read views that contain published data only."""
    if not {"researcher", "data_processor", "field_admin"}.intersection(user.roles):
        raise HTTPException(403, "当前账号没有已发布标准数据访问权限。")
    return user


async def require_knowledge_user(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    """Researchers browse knowledge; data processors curate institution/public sources."""
    if not {"researcher", "data_processor", "field_admin"}.intersection(user.roles):
        raise HTTPException(403, "当前账号没有知识库访问权限。")
    return user


async def require_knowledge_admin(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    """Only institution data processors may ingest or publish knowledge sources."""
    if "data_processor" not in user.roles:
        raise HTTPException(403, "公共知识资料仅允许数据处理员维护。")
    return user


async def require_genotype_user(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    """Allow legacy researcher assets and institution-level processor intake."""
    if not {"researcher", "data_processor"}.intersection(user.roles):
        raise HTTPException(403, "当前账号没有基因型数据访问权限。")
    return user


async def require_data_processor(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    if "data_processor" not in user.roles:
        raise HTTPException(403, "当前账号没有数据处理工作台访问权限。")
    return user


async def require_field_admin(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    if "field_admin" not in user.roles:
        raise HTTPException(403, "当前账号没有机构课题管理权限。")
    return user


async def require_data_platform_user(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    if not {"data_processor", "field_admin"}.intersection(user.roles):
        raise HTTPException(403, "当前账号没有数据集加工平台访问权限。")
    return user


async def require_trial_demo_user(user: CurrentUser = Security(get_current_user)) -> CurrentUser:
    """Read the governed multi-environment demo from any platform-facing role."""
    if not {"researcher", "data_processor", "field_admin"}.intersection(user.roles):
        raise HTTPException(403, "当前账号没有多环境试验数据访问权限。")
    return user


def audit_actor(user: CurrentUser) -> str:
    """Use the verified identity for audit logs instead of a client-supplied name."""
    return user.display_name or user.username
