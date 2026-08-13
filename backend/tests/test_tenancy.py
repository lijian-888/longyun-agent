import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.object_storage import (
    LocalObjectStore,
    ObjectStorageError,
    project_object_key,
    safe_object_key,
)
from app.tenancy import (
    TenantAccessError,
    TenantDatabaseManager,
    normalize_institution_id,
    workflow_queue_name,
)


class TenantIdentityTests(unittest.TestCase):
    def test_institution_and_queue_names_are_canonical(self) -> None:
        self.assertEqual(normalize_institution_id(" Org_A "), "org_a")
        self.assertEqual(workflow_queue_name("org_a"), "tenant.org-a.agent")

    def test_invalid_institution_identity_is_rejected(self) -> None:
        for value in ("", "A", "../../other", "org.a", "机构一"):
            with self.subTest(value=value), self.assertRaises(TenantAccessError):
                normalize_institution_id(value)

    def test_single_tenant_manager_fails_closed_for_another_institution(self) -> None:
        with patch.dict(os.environ, {
            "TENANCY_MODE": "single",
            "DEFAULT_INSTITUTION_ID": "org-a",
            "DATABASE_URL": "postgresql+psycopg://unused/app",
            "MIGRATION_DATABASE_URL": "postgresql+psycopg://unused/admin",
        }):
            manager = TenantDatabaseManager()
            self.assertEqual(manager.resolve("org-a").institution_id, "org-a")
            with self.assertRaises(TenantAccessError):
                manager.resolve("org-b")


class InstitutionObjectStorageTests(unittest.TestCase):
    def test_local_storage_cannot_read_another_institution_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            org_a = LocalObjectStore("org-a", root)
            org_b = LocalObjectStore("org-b", root)
            stored = org_a.put_bytes(
                "projects/project-1/imports/job-1/data.csv",
                b"material_id,trait\nA001,height\n",
                "text/csv",
            )
            self.assertEqual(org_a.read_bytes(stored.locator), b"material_id,trait\nA001,height\n")
            with self.assertRaises(ObjectStorageError):
                org_b.read_bytes(stored.locator)
            self.assertFalse(org_b.exists(stored.locator))

    def test_object_key_rejects_path_traversal(self) -> None:
        with self.assertRaises(ObjectStorageError):
            safe_object_key("projects/../../org-b/private.csv")

    def test_local_storage_streams_files_without_sharing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "large-source.bin"
            source.write_bytes((b"longyun" * 1024) + b"end")
            destination = root / "download" / "large-copy.bin"
            store = LocalObjectStore("org-a", root / "objects")

            stored = store.put_file("projects/shared/genotype/raw.bin", source)
            store.copy_to(stored.locator, destination)

            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(stored.size_bytes, source.stat().st_size)
            self.assertEqual(len(stored.sha256), 64)

    def test_project_key_keeps_project_and_owner_boundaries(self) -> None:
        key = project_object_key(
            project_id="project-8",
            category="research-attachments",
            resource_id="session-3",
            owner_user_id="user-9",
            file_name="田间数据 2026.csv",
        )
        self.assertEqual(
            key,
            "projects/project-8/research-attachments/session-3/users/user-9/田间数据_2026.csv",
        )


if __name__ == "__main__":
    unittest.main()
