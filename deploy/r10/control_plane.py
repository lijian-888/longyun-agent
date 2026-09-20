"""Internal, read-only operations control plane for one shared Longyun stack.

The HTTP listener is Docker-network only. Reconciliation is a separate CLI
operation and never creates buckets or changes business records.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

os.umask(0o077)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(action: str, result: str, **details: object) -> None:
    directory = os.environ.get("R10_AUDIT_DIR", "/var/lib/longyun-r10")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, "control-plane.jsonl")
    record = {"time": _now(), "action": action, "result": result, **details}
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def _database():
    import psycopg

    return psycopg.connect(os.environ["CONTROL_PLANE_DB_DSN"], connect_timeout=5)


def _object_store():
    from minio import Minio

    return Minio(
        os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=False,
    )


def _institution_configs() -> list[tuple[str, str, str, str]]:
    with _database() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT institution_id, minio_bucket, business_database, status "
            "FROM institution_data_config ORDER BY institution_id"
        )
        return list(cursor.fetchall())


def _anonymous_policy(client, bucket: str) -> tuple[bool, str | None]:
    """No bucket policy means S3 anonymous access is denied by default."""
    from minio.error import S3Error

    try:
        policy = client.get_bucket_policy(bucket)
    except S3Error as error:
        if error.code in {"NoSuchBucketPolicy", "NoSuchBucket"}:
            if error.code == "NoSuchBucket":
                raise RuntimeError(f"机构 Bucket 不存在：{bucket}") from error
            return True, None
        raise
    if not policy:
        return True, None
    return False, hashlib.sha256(policy.encode("utf-8")).hexdigest()


def reconcile_storage_policy() -> dict[str, object]:
    """Remove anonymous bucket policies only for registered institution buckets."""
    client = _object_store()
    configs = _institution_configs()
    if not configs:
        raise RuntimeError("机构配置为空，拒绝执行存储策略调整")
    changed: list[str] = []
    for institution_id, bucket, _database_name, status in configs:
        if status != "active" or not client.bucket_exists(bucket):
            raise RuntimeError(f"机构 {institution_id} 的 Bucket 不可用，已停止策略调整")
        is_private, policy_hash = _anonymous_policy(client, bucket)
        if not is_private:
            client.delete_bucket_policy(bucket)
            _audit("revoke_anonymous_bucket_policy", "success", institution_id=institution_id,
                   bucket=bucket, old_policy_sha256=policy_hash)
            changed.append(bucket)
        private_after, _ = _anonymous_policy(client, bucket)
        if not private_after:
            _audit("verify_private_bucket", "failed", institution_id=institution_id, bucket=bucket)
            raise RuntimeError(f"机构 Bucket 仍存在匿名策略：{bucket}")
    _audit("reconcile_storage_policy", "success", checked=len(configs), changed=changed)
    return {"checked": len(configs), "changed": changed, "private": True}


def _probe(url: str) -> bool:
    try:
        with urlopen(url, timeout=5) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def status() -> tuple[int, dict[str, object]]:
    checks = {
        "api": _probe(os.environ.get("CONTROL_PLANE_API_URL", "http://api:8000/api/health")),
        "web": _probe(os.environ.get("CONTROL_PLANE_WEB_URL", "http://web/")),
        "keycloak": _probe(os.environ.get("CONTROL_PLANE_KEYCLOAK_URL", "http://keycloak:8080/auth/realms/rice-research/.well-known/openid-configuration")),
        "minio": _probe(os.environ.get("CONTROL_PLANE_MINIO_HEALTH_URL", "http://minio:9000/minio/health/live")),
    }
    institutions: list[dict[str, object]] = []
    queues: list[dict[str, object]] = []
    try:
        configs = _institution_configs()
        client = _object_store()
        for institution_id, bucket, database_name, state in configs:
            private, _ = _anonymous_policy(client, bucket)
            institutions.append({"id": institution_id, "bucket": bucket,
                                 "business_database": database_name, "state": state,
                                 "bucket_private": private})
        with _database() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT institution_id, status, count(*) FROM ai_gateway_task "
                "WHERE status IN ('queued', 'running', 'paused') "
                "GROUP BY institution_id, status ORDER BY institution_id, status"
            )
            queues = [{"namespace": institution_id, "status": state, "count": count}
                      for institution_id, state, count in cursor.fetchall()]
        checks["database"] = True
        checks["storage_policy"] = bool(configs) and all(
            row["state"] == "active" and row["bucket_private"] for row in institutions
        )
        checks["queue_namespace"] = all(
            row["namespace"] in {item["id"] for item in institutions} for row in queues
        )
    except Exception:
        # Do not return exception strings: database DSNs and S3 SDK errors may
        # contain credentials or internal object paths.
        checks["database"] = False
        checks["storage_policy"] = False
        checks["queue_namespace"] = False
    healthy = all(checks.values())
    return (200 if healthy else 503), {
        "status": "ok" if healthy else "degraded", "checked_at": _now(),
        "checks": checks, "institutions": institutions, "queue_namespaces": queues,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/healthz", "/status"}:
            self.send_error(404)
            return
        code, body = status()
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"serve", "check", "reconcile"}:
        raise SystemExit("usage: control_plane.py {serve|check|reconcile}")
    if sys.argv[1] == "serve":
        ThreadingHTTPServer(("0.0.0.0", 8010), Handler).serve_forever()
    elif sys.argv[1] == "reconcile":
        print(json.dumps(reconcile_storage_policy(), ensure_ascii=False))
    else:
        code, payload = status()
        print(json.dumps(payload, ensure_ascii=False))
        raise SystemExit(0 if code == 200 else 1)
