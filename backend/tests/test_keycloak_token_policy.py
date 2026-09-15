import json
import unittest
from pathlib import Path


class KeycloakTokenPolicyTests(unittest.TestCase):
    def test_access_token_lifespan_is_ten_minutes(self):
        realm_file = Path(__file__).resolve().parents[2] / "deploy" / "keycloak" / "rice-research-realm.json"
        realm = json.loads(realm_file.read_text(encoding="utf-8"))
        self.assertEqual(realm["accessTokenLifespan"], 10 * 60)
        web_client = next(item for item in realm["clients"] if item["clientId"] == "rice-research-web")
        self.assertTrue(web_client["standardFlowEnabled"])
        self.assertFalse(web_client["directAccessGrantsEnabled"])


if __name__ == "__main__":
    unittest.main()
