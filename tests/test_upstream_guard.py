"""The watchdog must favor preserving a live generation over fast recovery."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import upstream_guard as guard


class UpstreamGuardTests(unittest.TestCase):
    def test_run_once_recovers_only_after_three_minutes_and_safety_check(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "state/state.json"
            container = {"instance": "container-a:startup-a", "running": True}
            fake_fcntl = SimpleNamespace(LOCK_EX=2, flock=lambda _fd, _mode: None)
            with patch.object(guard, "fcntl", fake_fcntl), \
                 patch.object(guard, "docker_state", return_value=container), \
                 patch.object(guard, "tcp_open", return_value=False), \
                 patch.object(guard, "active_image_work", return_value=False) as active, \
                 patch.object(guard, "docker_action") as action:
                self.assertEqual(guard.run_once(root, state_file, now=1000), "waiting")
                self.assertEqual(guard.run_once(root, state_file, now=1179), "waiting")
                active.assert_not_called()
                action.assert_not_called()
                self.assertEqual(guard.run_once(root, state_file, now=1180), "restart")
                action.assert_called_once_with("restart")
            self.assertIsNone(guard.read_state(state_file)["first_failure"])
            with patch.object(guard, "fcntl", fake_fcntl), \
                 patch.object(guard, "docker_state", return_value=container), \
                 patch.object(guard, "tcp_open", return_value=False), \
                 patch.object(guard, "active_image_work", side_effect=guard.SafetyCheckFailed("unreadable")), \
                 patch.object(guard, "docker_action") as action:
                self.assertEqual(guard.run_once(root, state_file, now=2000), "waiting")
                self.assertEqual(guard.run_once(root, state_file, now=2180), "deferred")
                action.assert_not_called()

    def test_only_sustained_failure_allows_restart(self):
        container = {"instance": "container-a:startup-a", "running": True}
        action, state = guard.decide({}, container, False, 1000)
        self.assertEqual(action, "waiting")
        self.assertEqual(guard.decide(state, container, False, 1179)[0], "waiting")
        self.assertEqual(guard.decide(state, container, False, 1180)[0], "restart")
        self.assertEqual(guard.decide(state, container, False, 1180, active=True)[0], "deferred")
        self.assertEqual(guard.decide(state, container, True, 1180),
                         ("healthy", {"instance": container["instance"], "first_failure": None}))
        self.assertEqual(guard.decide(state, {"instance": "container-a:startup-b", "running": True}, False, 1180)[0], "waiting")
        self.assertEqual(guard.decide(state, {"instance": "container-a:startup-a", "running": False}, False, 1180)[0], "start")

    def test_recent_customer_or_gateway_job_prevents_restart(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            with closing(sqlite3.connect(root / "data/astrogate.db")) as db:
                db.execute("CREATE TABLE generation_jobs (created_at TEXT, status TEXT)")
                db.execute("INSERT INTO generation_jobs VALUES (?, 'pending')", (datetime.now(timezone.utc).isoformat(),))
                db.commit()
            with patch.object(guard, "established_requests", return_value=False):
                self.assertTrue(guard.active_image_work(root, time.time()))
            with closing(sqlite3.connect(root / "data/astrogate.db")) as db:
                db.execute("DELETE FROM generation_jobs")
                db.execute("INSERT INTO generation_jobs VALUES (?, 'pending')", (datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat(),))
                db.commit()
            with closing(sqlite3.connect(root / "data/gateway-bridge.sqlite3")) as db:
                db.execute("CREATE TABLE image_jobs (created REAL, status TEXT)")
                db.execute("INSERT INTO image_jobs VALUES (?, 'pending')", (time.time(),))
                db.commit()
            with patch.object(guard, "established_requests", return_value=False):
                self.assertTrue(guard.active_image_work(root, time.time()))

    def test_upstream_async_task_and_unreadable_state_fail_safe(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            with closing(sqlite3.connect(root / "data/astrogate.db")) as db:
                db.execute("CREATE TABLE generation_jobs (created_at TEXT, status TEXT)")
                db.commit()
            (root / "upstream-data").mkdir()
            tasks = root / "upstream-data/image_tasks.json"
            tasks.write_text(json.dumps({"tasks": [{"status": "running", "created_ts": time.time()}]}), encoding="utf-8")
            with patch.object(guard, "established_requests", return_value=False):
                self.assertTrue(guard.active_image_work(root, time.time()))
                tasks.write_text("{", encoding="utf-8")
                with self.assertRaises(guard.SafetyCheckFailed):
                    guard.active_image_work(root, time.time())
            with patch.object(guard, "established_requests", return_value=True):
                self.assertTrue(guard.active_image_work(root, time.time()))


if __name__ == "__main__":
    unittest.main()
