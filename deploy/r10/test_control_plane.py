"""Dependency-free checks for R10 storage and namespace safety rules."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import control_plane


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query):
        return None

    def fetchall(self):
        return [("hainan-nanfan", "paused", 1)]


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return Cursor()


class ControlPlaneTests(unittest.TestCase):
    def test_status_has_private_bucket_and_institution_namespace(self):
        with patch.object(control_plane, "_probe", return_value=True), \
             patch.object(control_plane, "_institution_configs", return_value=[
                 ("hainan-nanfan", "longyun-hnnf", "longyun_hnnf", "active")
             ]), patch.object(control_plane, "_object_store"), \
             patch.object(control_plane, "_anonymous_policy", return_value=(True, None)), \
             patch.object(control_plane, "_database", return_value=Connection()):
            code, result = control_plane.status()
        self.assertEqual(code, 200)
        self.assertTrue(result["checks"]["storage_policy"])
        self.assertTrue(result["checks"]["queue_namespace"])
        self.assertEqual(result["queue_namespaces"][0]["namespace"], "hainan-nanfan")

    def test_reconcile_rejects_unregistered_buckets(self):
        with patch.object(control_plane, "_institution_configs", return_value=[]), \
             patch.object(control_plane, "_object_store"):
            with self.assertRaisesRegex(RuntimeError, "机构配置为空"):
                control_plane.reconcile_storage_policy()

    def test_reconcile_revokes_public_policy_and_audits(self):
        client = MagicMock()
        client.bucket_exists.return_value = True
        with patch.object(control_plane, "_institution_configs", return_value=[
                 ("hainan-nanfan", "longyun-hnnf", "longyun_hnnf", "active")
             ]), patch.object(control_plane, "_object_store", return_value=client), \
             patch.object(control_plane, "_anonymous_policy", side_effect=[
                 (False, "old-policy-hash"), (True, None)
             ]), patch.object(control_plane, "_audit") as audit:
            result = control_plane.reconcile_storage_policy()
        client.delete_bucket_policy.assert_called_once_with("longyun-hnnf")
        self.assertEqual(result["changed"], ["longyun-hnnf"])
        self.assertGreaterEqual(audit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
