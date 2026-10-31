import importlib.util
import unittest
from collections import Counter
from pathlib import Path


path = Path(__file__).resolve().parents[1] / "deploy" / "refresh_image_account_routes.py"
spec = importlib.util.spec_from_file_location("refresh_image_account_routes", path)
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)


class ImageAccountRoutesTests(unittest.TestCase):
    def test_ready_accounts_use_at_most_five_per_exit(self):
        accounts = [{"email": f"person-{number:02d}", "access_token": f"token-{number}",
                     "quota": 1, "status": "正常"} for number in range(78)]
        accounts.append({"email": "disabled", "access_token": "disabled", "quota": 10, "status": "禁用"})
        ready = routes.ready_accounts(accounts)
        assignments = routes.route_assignments(ready, 16)
        counts = Counter(proxy for _, proxy in assignments)
        self.assertEqual(len(ready), 78)
        self.assertEqual(len(counts), 16)
        self.assertLessEqual(max(counts.values()), 5)
        with self.assertRaises(ValueError):
            routes.route_assignments(ready, 15)

    def test_hong_kong_and_metadata_are_never_routed(self):
        self.assertFalse(routes.eligible_node({"name": "香港01", "server": "example.com"}))
        self.assertFalse(routes.eligible_node({"name": "Hong Kong 02", "server": "example.com"}))
        self.assertFalse(routes.eligible_node({"name": "剩余流量：100 GB", "server": "example.com"}))
        self.assertTrue(routes.eligible_node({"name": "日本01", "server": "example.com"}))

    def test_each_exit_has_automatic_fallback(self):
        verified = [(100 + index, f"node-{index}") for index in range(5)]
        nodes = [{"name": name, "server": "example.com"} for _, name in verified]
        config = routes.production_config({"mixed-port": 17891}, nodes, {}, verified, 4)
        self.assertEqual([listener["port"] for listener in config["listeners"]], [17911, 17912, 17913, 17914])
        for index, group in enumerate(config["proxy-groups"][1:]):
            self.assertEqual(group["proxies"][0], f"node-{index}")
            self.assertEqual(group["type"], "fallback")
            self.assertGreater(len(group["proxies"]), 1)


if __name__ == "__main__":
    unittest.main()
