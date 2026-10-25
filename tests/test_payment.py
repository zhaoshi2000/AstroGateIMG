from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import unittest

from fastapi.testclient import TestClient

from app.main import Settings, create_app, easy_pay_sign


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key = "test-merchant-key"
        self.client = TestClient(create_app(Settings(
            data_dir=Path(self.directory.name), upstream_url="", upstream_api_key="",
            cookie_secure=False, admin_username="operator", admin_password="test-password-123",
            payment_api_base="https://pay.example", payment_pid="M123", payment_key=self.key,
            gateway_public_origin="https://img.example")))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        response = self.client.post("/api/auth/login", json={"identifier": "operator", "password": "test-password-123"})
        self.assertEqual(response.status_code, 200)

    def order(self, tier):
        response = self.client.post("/api/payments/orders", json={"tier": tier})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def notify(self, order, trade_no="TR123", **changes):
        fields = {"pid": "M123", "trade_no": trade_no, "out_trade_no": order["id"],
                  "type": "alipay", "name": order["fields"]["name"],
                  "money": order["fields"]["money"], "trade_status": "TRADE_SUCCESS", **changes}
        fields["sign"] = easy_pay_sign(fields, self.key)
        fields["sign_type"] = "MD5"
        return self.client.get("/api/payments/notify", params=fields)

    def test_tiers_and_signed_order_form(self):
        for yuan, credits in ((1, 20), (5, 120), (10, 300)):
            order = self.order(yuan)
            self.assertEqual(order["credits"], credits)
            self.assertEqual(order["amount_cents"], yuan * 100)
            self.assertEqual(order["action_url"], "https://pay.example/submit.php")
            self.assertEqual(order["fields"]["type"], "alipay")
            self.assertEqual(order["fields"]["sign"], easy_pay_sign(order["fields"], self.key))
        self.assertEqual(self.client.post("/api/payments/orders", json={"tier": 2}).status_code, 422)
        self.assertEqual(self.client.get("/api/wallet").json()["credits"], 0)

    def test_callback_credits_once_and_rejects_tampering(self):
        order = self.order(5)
        bad = dict(order["fields"], money="1.00")
        self.assertEqual(self.client.get("/api/payments/notify", params=bad).status_code, 400)
        self.assertEqual(self.notify(order, money="1.00").status_code, 400)
        self.assertEqual(self.client.get("/api/wallet").json()["credits"], 0)
        first = self.notify(order)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.text, "success")
        self.assertEqual(self.notify(order).status_code, 200)
        self.assertEqual(self.notify(order, trade_no="DIFFERENT").status_code, 400)
        wallet = self.client.get("/api/wallet").json()
        self.assertEqual(wallet["credits"], 120)
        self.assertEqual(len([entry for entry in wallet["ledger"] if entry["kind"] == "payment"]), 1)
        self.assertEqual(self.client.get("/api/payments/orders/" + order["id"]).json()["status"], "paid")

    def test_trade_no_cannot_credit_two_orders(self):
        first = self.order(1)
        second = self.order(1)
        self.assertEqual(self.notify(first).status_code, 200)
        self.assertEqual(self.notify(second).status_code, 400)
        self.assertEqual(self.client.get("/api/wallet").json()["credits"], 20)
        self.assertEqual(self.client.get("/api/payments/orders/" + second["id"]).json()["status"], "pending")

    def test_concurrent_notifications_credit_once(self):
        order = self.order(10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.notify(order), range(2)))
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(self.client.get("/api/wallet").json()["credits"], 300)
        self.assertEqual(len([row for row in self.client.get("/api/wallet").json()["ledger"] if row["kind"] == "payment"]), 1)


if __name__ == "__main__":
    unittest.main()
