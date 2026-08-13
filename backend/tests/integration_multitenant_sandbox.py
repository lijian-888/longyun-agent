"""Disposable acceptance check for real two-institution sandbox isolation."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text

from app.tenancy import TenantAccessError, tenant_database_manager


ORG_A_USER = "10000000-0000-4000-8000-000000000001"
ORG_B_USER = "20000000-0000-4000-8000-000000000001"


def main() -> None:
    tenant_database_manager.ensure_control_schema()
    bindings = {item.institution_id: item for item in tenant_database_manager.active_bindings()}
    assert set(bindings) == {"org-a", "org-b"}, set(bindings)

    tenant_database_manager.verify_user_membership(ORG_A_USER, "org-a")
    tenant_database_manager.verify_user_membership(ORG_B_USER, "org-b")
    try:
        tenant_database_manager.verify_user_membership(ORG_A_USER, "org-b")
    except TenantAccessError:
        pass
    else:
        raise AssertionError("org-a account was accepted by org-b")

    sentinels: dict[str, str] = {}
    for institution_id in ("org-a", "org-b"):
        engine = tenant_database_manager.engine_for(institution_id, migration=True)
        with engine.begin() as connection:
            metadata_id = connection.execute(text(
                "SELECT institution_id FROM tenant_metadata WHERE singleton=true"
            )).scalar_one()
            assert metadata_id == institution_id, (metadata_id, institution_id)
            sentinel = f"acceptance:{institution_id}:{uuid4()}"
            sentinels[institution_id] = sentinel
            connection.execute(text("""
                INSERT INTO ai_usage_log(
                    id, request_id, institution_id, owner_id, route,
                    provider_name, provider_host, model_alias, status
                ) VALUES (
                    :id, :request_id, :institution_id, :owner_id, 'acceptance',
                    'acceptance-provider', 'no-network', 'acceptance-model', 'completed'
                )
            """), {
                "id": str(uuid4()),
                "request_id": sentinel,
                "institution_id": institution_id,
                "owner_id": ORG_A_USER if institution_id == "org-a" else ORG_B_USER,
            })

    for institution_id, other_id in (("org-a", "org-b"), ("org-b", "org-a")):
        engine = tenant_database_manager.engine_for(institution_id, migration=True)
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM ai_usage_log WHERE request_id=:request_id"
            ), {"request_id": sentinels[institution_id]}).scalar_one() == 1
            assert connection.execute(text(
                "SELECT count(*) FROM ai_usage_log WHERE request_id=:request_id"
            ), {"request_id": sentinels[other_id]}).scalar_one() == 0

    # A platform-side status change must invalidate existing JWT-backed access
    # without waiting for the identity provider token to expire.
    with tenant_database_manager.control_engine.begin() as connection:
        connection.execute(text("""
            UPDATE institution_user SET status='disabled', updated_at=now()
            WHERE keycloak_user_id=:user_id
        """), {"user_id": ORG_A_USER})
    try:
        tenant_database_manager.verify_user_membership(ORG_A_USER, "org-a")
    except TenantAccessError:
        pass
    else:
        raise AssertionError("disabled account retained platform access")
    finally:
        with tenant_database_manager.control_engine.begin() as connection:
            connection.execute(text("""
                UPDATE institution_user SET status='active', updated_at=now()
                WHERE keycloak_user_id=:user_id
            """), {"user_id": ORG_A_USER})

    print("multitenant-database-account-and-usage-isolation-ok")


if __name__ == "__main__":
    main()
