"""Create the isolated production demonstration used by Task 4–7 acceptance.

This operational helper is intentionally idempotent and must be given an
existing project id.  It publishes one governed regional-trial package and one
authorized institution-literature derivative for that project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from app.main import (
    KNOWLEDGE_STORAGE_DIR,
    RAW_STORAGE_DIR,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeFolder,
    MigrationSessionLocal,
    process_knowledge_document,
    seed_public_knowledge_folders,
)
from app.trial_package import publish_trial_package, upload_trial_package


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--literature", type=Path, required=True)
    args = parser.parse_args()

    archive = args.archive.read_bytes()
    literature_text = args.literature.read_text(encoding="utf-8").strip()
    if not literature_text:
        raise RuntimeError("literature fixture is empty")

    trial_result: dict[str, object]
    with MigrationSessionLocal() as session:
        existing = session.execute(
            text(
                "SELECT id, package_code FROM trial_data_package "
                "WHERE project_id=:project_id AND governance_status='published' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"project_id": args.project_id},
        ).mappings().first()
        if existing:
            trial_result = {"status": "already_published", **dict(existing)}
        else:
            batch = upload_trial_package(
                session,
                args.archive.name,
                archive,
                "task4-7-acceptance",
                RAW_STORAGE_DIR,
                args.project_id,
            )
            if batch.get("parse_status") != "ready_for_review":
                raise RuntimeError(f"trial package is not publishable: {batch}")
            trial_result = publish_trial_package(
                session,
                str(batch["id"]),
                "task4-7-acceptance",
                args.project_id,
            )

        seed_public_knowledge_folders(session, args.project_id)
        folder = session.scalar(
            select(KnowledgeFolder).where(
                KnowledgeFolder.project_id == args.project_id,
                KnowledgeFolder.scope == "public",
                KnowledgeFolder.folder_name == "论文与综述",
            )
        )
        if not folder:
            raise RuntimeError("public literature folder was not seeded")

        content = literature_text.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        document = session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.project_id == args.project_id,
                KnowledgeDocument.scope == "public",
                KnowledgeDocument.content_hash == digest,
                KnowledgeDocument.status.not_in(("withdrawn", "superseded", "deleted")),
            )
        )
        created = document is None
        if document is None:
            document_id = str(uuid.uuid4())
            directory = KNOWLEDGE_STORAGE_DIR / args.project_id / "public" / "public" / document_id
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "institution-literature-demo.txt"
            path.write_bytes(content)
            document = KnowledgeDocument(
                id=document_id,
                project_id=args.project_id,
                scope="public",
                owner_id="task4-7-acceptance",
                folder_id=folder.id,
                original_file_name=args.literature.name,
                display_title="海南南繁水稻育种公开资料验收样例",
                content_type="text/plain; charset=utf-8",
                size_bytes=len(content),
                content_hash=digest,
                storage_path=str(path),
                source_organization="海南南繁育种知识库",
                author="隆耘智能体项目组",
                publication_year="2026",
                source_url="institution://hainan-nanfan/task4-7-acceptance/literature",
                short_description="由机构治理数据同步形成的公开资料检索样例。",
                authorization_basis="公开资料或合法授权资料，由数据处理员导入并经字段管理员验收发布。",
                license_scope="仅限海南南繁课题内科研检索、证据引用与验收演示",
                topic_tags=["品种", "基因", "性状", "育种目标", "试验"],
                status="processing",
            )
            session.add(document)
            session.commit()
        document_id = document.id

    if created:
        process_knowledge_document(document_id)

    with MigrationSessionLocal() as session:
        document = session.get(KnowledgeDocument, document_id)
        chunk_count = int(
            session.scalar(
                select(text("count(*)")).select_from(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == document_id
                )
            )
            or 0
        )
        if not document or document.parsing_status != "parsed" or document.indexing_status != "ready" or chunk_count < 1:
            raise RuntimeError(
                "knowledge document did not pass parsing/indexing: "
                f"parsing={getattr(document, 'parsing_status', None)} "
                f"indexing={getattr(document, 'indexing_status', None)} chunks={chunk_count}"
            )
        document.status = "published"
        document.published_at = datetime.now(timezone.utc)
        session.execute(
            text("UPDATE knowledge_chunk SET document_status='published' WHERE document_id=:document_id"),
            {"document_id": document_id},
        )
        session.commit()

    print(
        json.dumps(
            {
                "status": "passed",
                "project_id": args.project_id,
                "trial": trial_result,
                "knowledge": {
                    "document_id": document_id,
                    "chunk_count": chunk_count,
                    "authorization_complete": True,
                    "published": True,
                },
            },
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
