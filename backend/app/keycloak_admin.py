"""Narrow Keycloak account lifecycle client used by the offline admin CLI."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx


class KeycloakAdminError(RuntimeError):
    """Identity operation failed without exposing credentials or response bodies."""


class KeycloakAdminClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("KEYCLOAK_ADMIN_URL", "").strip().rstrip("/")
        self.admin_realm = os.getenv("KEYCLOAK_ADMIN_REALM", "master").strip()
        self.managed_realm = os.getenv("KEYCLOAK_MANAGED_REALM", "rice-research").strip()
        self.username = os.getenv("KEYCLOAK_ADMIN_USERNAME", "").strip()
        self.password = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "")
        if not self.base_url or not self.username or not self.password:
            raise KeycloakAdminError(
                "KEYCLOAK_ADMIN_URL/USERNAME/PASSWORD are required for account lifecycle commands"
            )

    def _token(self) -> str:
        try:
            with httpx.Client(timeout=12) as client:
                response = client.post(
                    f"{self.base_url}/realms/{self.admin_realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "admin-cli",
                        "username": self.username,
                        "password": self.password,
                    },
                )
                response.raise_for_status()
                token = str(response.json().get("access_token") or "")
        except Exception as exc:
            raise KeycloakAdminError("unable to authenticate to Keycloak admin API") from exc
        if not token:
            raise KeycloakAdminError("Keycloak admin API returned no access token")
        return token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict | None = None,
    ) -> Any:
        try:
            with httpx.Client(timeout=12) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self._token()}"},
                    json=json_payload,
                )
                response.raise_for_status()
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()
        except KeycloakAdminError:
            raise
        except Exception as exc:
            raise KeycloakAdminError(f"Keycloak admin operation failed: {method} {path}") from exc

    def _request(self, method: str, path: str, *, json_payload: dict | None = None) -> None:
        self._request_json(method, path, json_payload=json_payload)

    @property
    def _users_path(self) -> str:
        return f"/admin/realms/{self.managed_realm}/users"

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._request("PUT", f"{self._users_path}/{encoded_user_id}", json_payload={"enabled": enabled})
        if not enabled:
            self.logout(user_id)

    def logout(self, user_id: str) -> None:
        self._request("POST", f"{self._users_path}/{quote(user_id, safe='')}/logout")

    def delete(self, user_id: str) -> None:
        self._request("DELETE", f"{self._users_path}/{quote(user_id, safe='')}")

    def institution_users(self, institution_id: str) -> list[dict[str, Any]]:
        """Return a narrow, institution-filtered identity directory for project assignment."""
        normalized_institution = (institution_id or "").strip()
        tenancy_mode = os.getenv("TENANCY_MODE", "single").strip().lower()
        default_institution = os.getenv("DEFAULT_INSTITUTION_ID", "longyun-demo").strip()
        # Keycloak's default brief representation omits custom attributes, so
        # institution_id would otherwise be invisible and every institution
        # directory would appear empty.
        users = self._request_json(
            "GET",
            f"{self._users_path}?first=0&max=1000&briefRepresentation=false",
        ) or []
        result: list[dict[str, Any]] = []
        for item in users:
            attributes = item.get("attributes") or {}
            raw_institution = attributes.get("institution_id") or []
            if isinstance(raw_institution, str):
                raw_institution = [raw_institution]
            institutions = {str(value).strip() for value in raw_institution if str(value).strip()}
            belongs_to_institution = normalized_institution in institutions
            legacy_single_tenant_account = (
                not institutions
                and tenancy_mode == "single"
                and normalized_institution == default_institution
            )
            if not belongs_to_institution and not legacy_single_tenant_account:
                continue
            user_id = str(item.get("id") or "").strip()
            if not user_id:
                continue
            role_rows = self._request_json(
                "GET",
                f"{self._users_path}/{quote(user_id, safe='')}/role-mappings/realm",
            ) or []
            platform_roles = sorted({
                str(role.get("name") or "").strip()
                for role in role_rows
                if str(role.get("name") or "").strip() in {"field_admin", "data_processor", "researcher"}
            })
            display_name = " ".join(
                value for value in [str(item.get("firstName") or "").strip(), str(item.get("lastName") or "").strip()]
                if value
            )
            result.append({
                "user_id": user_id,
                "username": str(item.get("username") or "").strip(),
                "display_name": display_name or str(item.get("username") or "").strip() or user_id,
                "platform_roles": platform_roles,
                "enabled": bool(item.get("enabled", True)),
            })
        return sorted(result, key=lambda row: (row["display_name"], row["username"], row["user_id"]))
