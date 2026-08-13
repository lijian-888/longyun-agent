"""Small control-plane CLI for institution and account lifecycle operations.

Run this inside the API image.  Resource bindings remain declarative in
``TENANT_BOOTSTRAP_JSON``; this command manages the mutable account/status
state without exposing control-plane credentials through an HTTP endpoint.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text

from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .tenancy import normalize_institution_id, tenant_database_manager


TENANT_STATUSES = {"trial", "active", "frozen", "pending_destroy", "destroyed"}


def _require_multi_tenant() -> None:
    if tenant_database_manager.mode != "multi":
        raise SystemExit("tenant-admin requires TENANCY_MODE=multi")
    tenant_database_manager.ensure_control_schema()


def bind_user(user_id: str, institution_id: str) -> None:
    institution_id = normalize_institution_id(institution_id)
    user_id = user_id.strip()
    if not user_id:
        raise SystemExit("Keycloak user id cannot be empty")
    with tenant_database_manager.control_engine.begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM institution WHERE id=:id"), {"id": institution_id}
        ).scalar_one_or_none()
        if not exists:
            raise SystemExit(f"unknown institution: {institution_id}")
        current_institution = connection.execute(text(
            "SELECT institution_id FROM institution_user WHERE keycloak_user_id=:user_id"
        ), {"user_id": user_id}).scalar_one_or_none()
        if current_institution and current_institution != institution_id:
            raise SystemExit(
                f"user is already bound to {current_institution}; explicit account migration is required"
            )
        connection.execute(text(
            """
            INSERT INTO institution_user(keycloak_user_id, institution_id, status)
            VALUES (:user_id, :institution_id, 'active')
            ON CONFLICT (keycloak_user_id) DO UPDATE SET
                status='active',
                updated_at=now()
            """
        ), {"user_id": user_id, "institution_id": institution_id})
    print(f"bound {user_id} -> {institution_id}")


def set_status(institution_id: str, status: str, retention_days: int) -> None:
    institution_id = normalize_institution_id(institution_id)
    status = status.strip().lower()
    if status not in TENANT_STATUSES:
        raise SystemExit(f"unsupported status: {status}")
    now = datetime.now(timezone.utc)
    purge_at = now + timedelta(days=retention_days) if status == "frozen" else None
    with tenant_database_manager.control_engine.begin() as connection:
        result = connection.execute(text(
            """
            UPDATE institution
            SET status=CAST(:status AS VARCHAR(30)),
                expires_at=CASE WHEN CAST(:status AS VARCHAR(30))='frozen' THEN now() ELSE expires_at END,
                purge_at=CASE WHEN CAST(:status AS VARCHAR(30))='frozen' THEN :purge_at
                              WHEN CAST(:status AS VARCHAR(30)) IN ('trial', 'active') THEN NULL
                              ELSE purge_at END,
                updated_at=now()
            WHERE id=:institution_id
            """
        ), {
            "institution_id": institution_id,
            "status": status,
            "purge_at": purge_at,
        })
        if result.rowcount != 1:
            raise SystemExit(f"unknown institution: {institution_id}")
    tenant_database_manager.clear_binding_cache()
    print(f"institution {institution_id} -> {status}")
    if purge_at:
        print(f"retention deadline: {purge_at.isoformat()}")


def list_tenants() -> None:
    with tenant_database_manager.control_engine.connect() as connection:
        rows = connection.execute(text(
            """
            SELECT i.id, i.display_name, i.status, i.expires_at, i.purge_at,
                   b.storage_backend, b.data_bucket, b.queue_prefix,
                   count(u.keycloak_user_id) AS user_count
            FROM institution i
            JOIN tenant_resource_binding b ON b.institution_id=i.id
            LEFT JOIN institution_user u ON u.institution_id=i.id AND u.status='active'
            GROUP BY i.id, i.display_name, i.status, i.expires_at, i.purge_at,
                     b.storage_backend, b.data_bucket, b.queue_prefix
            ORDER BY i.id
            """
        )).mappings().all()
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, default=str, indent=2))


def list_users(institution_id: str | None = None) -> None:
    parameters: dict[str, str] = {}
    where = ""
    if institution_id:
        parameters["institution_id"] = normalize_institution_id(institution_id)
        where = "WHERE u.institution_id=:institution_id"
    with tenant_database_manager.control_engine.connect() as connection:
        rows = connection.execute(text(f"""
            SELECT u.keycloak_user_id, u.institution_id, u.status,
                   u.created_at, u.updated_at
            FROM institution_user u
            {where}
            ORDER BY u.institution_id, u.created_at, u.keycloak_user_id
        """), parameters).mappings().all()
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, default=str, indent=2))


def usage_report(institution_id: str, days: int, limit: int) -> None:
    institution_id = normalize_institution_id(institution_id)
    with tenant_database_manager.engine_for(institution_id, migration=True).connect() as connection:
        exists = connection.execute(text(
            "SELECT to_regclass('public.ai_usage_log') IS NOT NULL"
        )).scalar_one()
        if not exists:
            print("[]")
            return
        rows = connection.execute(text("""
            SELECT owner_id,
                   count(*) AS request_count,
                   count(*) FILTER (WHERE status='completed') AS completed_count,
                   count(*) FILTER (WHERE status='failed') AS failed_count,
                   count(*) FILTER (WHERE status='cancelled') AS cancelled_count,
                   COALESCE(sum(total_tokens), 0) AS total_tokens,
                   COALESCE(sum(latency_ms), 0) AS total_latency_ms,
                   max(created_at) AS last_request_at
            FROM ai_usage_log
            WHERE institution_id=:institution_id
              AND created_at >= now() - (:days * interval '1 day')
            GROUP BY owner_id
            ORDER BY request_count DESC, last_request_at DESC
            LIMIT :limit
        """), {
            "institution_id": institution_id,
            "days": days,
            "limit": limit,
        }).mappings().all()
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, default=str, indent=2))


def _account_row(user_id: str) -> dict:
    with tenant_database_manager.control_engine.connect() as connection:
        row = connection.execute(text("""
            SELECT keycloak_user_id, institution_id, status
            FROM institution_user WHERE keycloak_user_id=:user_id
        """), {"user_id": user_id}).mappings().one_or_none()
    if not row:
        raise SystemExit(f"unknown platform account: {user_id}")
    return dict(row)


def _write_account_status(
    *,
    user_id: str,
    institution_id: str,
    status: str,
    action: str,
    operator_id: str,
    reason: str,
    identity_status: str,
) -> None:
    with tenant_database_manager.control_engine.begin() as connection:
        connection.execute(text("""
            UPDATE institution_user SET status=:status, updated_at=now()
            WHERE keycloak_user_id=:user_id AND institution_id=:institution_id
        """), {
            "status": status,
            "user_id": user_id,
            "institution_id": institution_id,
        })
        connection.execute(text("""
            INSERT INTO account_admin_audit(
                id, keycloak_user_id, institution_id, action,
                operator_id, reason, identity_provider_status
            ) VALUES (
                :id, :user_id, :institution_id, :action,
                :operator_id, :reason, :identity_status
            )
        """), {
            "id": str(uuid4()),
            "user_id": user_id,
            "institution_id": institution_id,
            "action": action,
            "operator_id": operator_id,
            "reason": reason,
            "identity_status": identity_status,
        })


def disable_user(user_id: str, operator_id: str, reason: str) -> None:
    row = _account_row(user_id)
    _write_account_status(
        user_id=user_id,
        institution_id=row["institution_id"],
        status="disabled",
        action="disable",
        operator_id=operator_id,
        reason=reason,
        identity_status="pending",
    )
    try:
        KeycloakAdminClient().set_enabled(user_id, False)
    except KeycloakAdminError as exc:
        _write_account_status(
            user_id=user_id,
            institution_id=row["institution_id"],
            status="disabled",
            action="disable_identity_failed",
            operator_id=operator_id,
            reason=reason,
            identity_status="failed",
        )
        raise SystemExit(f"platform access is blocked, but Keycloak disable failed: {exc}") from exc
    _write_account_status(
        user_id=user_id,
        institution_id=row["institution_id"],
        status="disabled",
        action="disable_identity_completed",
        operator_id=operator_id,
        reason=reason,
        identity_status="completed",
    )
    print(f"disabled account {user_id}; existing platform tokens are blocked")


def enable_user(user_id: str, operator_id: str, reason: str) -> None:
    row = _account_row(user_id)
    KeycloakAdminClient().set_enabled(user_id, True)
    _write_account_status(
        user_id=user_id,
        institution_id=row["institution_id"],
        status="active",
        action="enable",
        operator_id=operator_id,
        reason=reason,
        identity_status="completed",
    )
    print(f"enabled account {user_id}")


def delete_user(user_id: str, operator_id: str, reason: str, confirmation: str) -> None:
    if confirmation != user_id:
        raise SystemExit("delete confirmation must exactly match --user-id")
    row = _account_row(user_id)
    _write_account_status(
        user_id=user_id,
        institution_id=row["institution_id"],
        status="deleted",
        action="delete",
        operator_id=operator_id,
        reason=reason,
        identity_status="pending",
    )
    try:
        KeycloakAdminClient().delete(user_id)
    except KeycloakAdminError as exc:
        raise SystemExit(f"platform access is blocked, but Keycloak deletion failed: {exc}") from exc
    _write_account_status(
        user_id=user_id,
        institution_id=row["institution_id"],
        status="deleted",
        action="delete_identity_completed",
        operator_id=operator_id,
        reason=reason,
        identity_status="completed",
    )
    print(f"deleted identity {user_id}; research records remain under institution retention rules")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.tenant_admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init", help="create/control-plane schema and bootstrap bindings")
    bind = subcommands.add_parser("bind-user", help="bind one Keycloak subject to exactly one institution")
    bind.add_argument("--user-id", required=True)
    bind.add_argument("--institution", required=True)
    status = subcommands.add_parser("set-status", help="activate or freeze an institution")
    status.add_argument("--institution", required=True)
    status.add_argument("--status", choices=sorted(TENANT_STATUSES), required=True)
    status.add_argument("--retention-days", type=int, default=10)
    subcommands.add_parser("list", help="list registered institutions without printing secrets")
    users = subcommands.add_parser("users", help="list platform accounts and mutable access status")
    users.add_argument("--institution")
    usage = subcommands.add_parser("usage", help="aggregate privacy-preserving AI usage by account")
    usage.add_argument("--institution", required=True)
    usage.add_argument("--days", type=int, default=7)
    usage.add_argument("--limit", type=int, default=100)
    for command in ("disable-user", "enable-user", "delete-user"):
        account = subcommands.add_parser(command, help=f"{command} in platform and Keycloak")
        account.add_argument("--user-id", required=True)
        account.add_argument("--operator", required=True)
        account.add_argument("--reason", required=True)
        if command == "delete-user":
            account.add_argument("--confirm-user-id", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _require_multi_tenant()
    if args.command == "init":
        print("control plane initialized")
    elif args.command == "bind-user":
        bind_user(args.user_id, args.institution)
    elif args.command == "set-status":
        if args.retention_days < 0:
            raise SystemExit("retention-days cannot be negative")
        set_status(args.institution, args.status, args.retention_days)
    elif args.command == "list":
        list_tenants()
    elif args.command == "users":
        list_users(args.institution)
    elif args.command == "usage":
        if not 1 <= args.days <= 365 or not 1 <= args.limit <= 1000:
            raise SystemExit("days must be 1..365 and limit must be 1..1000")
        usage_report(args.institution, args.days, args.limit)
    elif args.command == "disable-user":
        disable_user(args.user_id, args.operator, args.reason)
    elif args.command == "enable-user":
        enable_user(args.user_id, args.operator, args.reason)
    elif args.command == "delete-user":
        delete_user(
            args.user_id,
            args.operator,
            args.reason,
            args.confirm_user_id,
        )


if __name__ == "__main__":
    main()
