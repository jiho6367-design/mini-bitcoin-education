"""Validate disk boundaries and privilege-drop ordering without a cloud service."""
import os
import importlib.util
from pathlib import Path
import signal
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deployment_entrypoint as entrypoint


class DeploymentEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name).resolve() / "data"
        self.environment = {"TEAM_DATA_DIR": str(self.root), "TEAM_TEACHER_SECRET": "test-" + "x" * 40}

    def tearDown(self):
        self.folder.cleanup()

    def test_both_databases_and_backups_stay_inside_the_explicit_data_directory(self):
        root, databases, backups = entrypoint.storage_paths(self.environment)
        self.assertEqual(root, self.root)
        self.assertEqual(databases, (self.root / "basic.sqlite3", self.root / "education.sqlite3"))
        self.assertEqual(backups, self.root / "backups")
        for key in ("KAKAO_PRACTICE_DB", "TEAM_EDUCATION_DB", "TEAM_BACKUP_DIR"):
            for value in (str(self.root.parent / "outside.sqlite3"), ":memory:", "relative.sqlite3"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    entrypoint.storage_paths({**self.environment, key: value})
        with self.assertRaises(ValueError):
            entrypoint.storage_paths({**self.environment, "TEAM_EDUCATION_DB": str(self.root / "basic.sqlite3")})

    def test_mounted_disk_setup_changes_only_configured_files_and_known_sidecars(self):
        self.root.mkdir()
        main_db = self.root / "education.sqlite3"
        main_db.write_bytes(b"existing educational record")
        sidecar = self.root / "education.sqlite3-wal"
        sidecar.write_bytes(b"existing WAL")
        unrelated = self.root / "leave-alone.txt"
        unrelated.write_text("preserve", encoding="utf-8")
        root, databases, backups = entrypoint.storage_paths(self.environment)
        with patch.object(entrypoint.os, "chown", create=True) as chown:
            entrypoint.prepare_storage(root, databases, backups, privileged=True)
        touched = {Path(call.args[0]) for call in chown.call_args_list}
        self.assertEqual(touched, {self.root, backups, main_db, sidecar})
        self.assertEqual(main_db.read_bytes(), b"existing educational record")
        self.assertEqual(sidecar.read_bytes(), b"existing WAL")
        self.assertNotIn(unrelated, touched)
        entrypoint.verify_writable(root, databases, backups)

    def test_privileges_are_dropped_before_write_probe_and_server_exec(self):
        order = []
        with patch.object(entrypoint.os, "geteuid", return_value=0, create=True), \
                patch.object(entrypoint.os, "umask", side_effect=lambda value: order.append(("umask", value))), \
                patch.object(entrypoint, "prepare_storage", side_effect=lambda *a, **k: order.append("disk")), \
                patch.object(entrypoint.os, "setgroups", side_effect=lambda value: order.append(("groups", value)), create=True), \
                patch.object(entrypoint.os, "setgid", side_effect=lambda value: order.append(("gid", value)), create=True), \
                patch.object(entrypoint.os, "setuid", side_effect=lambda value: order.append(("uid", value)), create=True), \
                patch.object(entrypoint, "verify_writable", side_effect=lambda *a: order.append("probe")), \
                patch.object(entrypoint.os, "execvpe", side_effect=lambda *a: order.append(("exec", a))):
            entrypoint.main(["gunicorn", "app:app"], dict(self.environment))
        self.assertEqual(order[:6], [("umask", 0o077), "disk", ("groups", []), ("gid", 10001), ("uid", 10001), "probe"])
        self.assertEqual(order[6][0], "exec")
        self.assertEqual(order[6][1][2]["HOME"], "/home/teamapp")
        self.assertEqual(order[6][1][2]["TEAM_EDUCATION_DB"], str(self.root / "education.sqlite3"))

    def test_failed_write_probe_prevents_server_start(self):
        with patch.object(entrypoint.os, "geteuid", return_value=10001, create=True), \
                patch.object(entrypoint.os, "umask"), \
                patch.object(entrypoint, "prepare_storage"), \
                patch.object(entrypoint, "verify_writable", side_effect=PermissionError("read-only volume")), \
                patch.object(entrypoint.os, "execvpe") as execute:
            with self.assertRaises(PermissionError):
                entrypoint.main(["gunicorn", "app:app"], dict(self.environment))
            execute.assert_not_called()

    def test_missing_teacher_secret_fails_without_touching_the_disk(self):
        with patch.object(entrypoint.os, "geteuid", return_value=0, create=True), \
                patch.object(entrypoint, "prepare_storage") as prepare:
            with self.assertRaisesRegex(ValueError, "TEAM_TEACHER_SECRET"):
                entrypoint.main([], {"TEAM_DATA_DIR": str(self.root)})
            prepare.assert_not_called()

    def test_postgres_deployment_drops_privileges_without_using_ephemeral_sqlite(self):
        environment = {"TEAM_TEACHER_SECRET": "test-" + "x" * 40,
                       "TEAM_DATABASE_URL": "postgresql://test:test@postgres/test?sslmode=disable"}
        with patch.object(entrypoint.os, "geteuid", return_value=0, create=True), \
                patch.object(entrypoint.os, "umask"), \
                patch.object(entrypoint, "prepare_storage") as prepare, \
                patch.object(entrypoint, "verify_writable") as probe, \
                patch.object(entrypoint.os, "setgroups", create=True), \
                patch.object(entrypoint.os, "setgid", create=True), \
                patch.object(entrypoint.os, "setuid", create=True) as drop, \
                patch.object(entrypoint.os, "execvpe") as execute:
            entrypoint.main(["gunicorn", "app:app"], environment)
        prepare.assert_not_called()
        probe.assert_not_called()
        drop.assert_called_once_with(10001)
        self.assertEqual(execute.call_args.args[0], "gunicorn")
        self.assertNotIn("TEAM_EDUCATION_DB", execute.call_args.args[2])
        self.assertNotIn("KAKAO_PRACTICE_DB", execute.call_args.args[2])


class GunicornShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
        spec = importlib.util.spec_from_file_location("team_gunicorn_config", path)
        cls.config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.config)

    def test_sigterm_stops_socket_loops_before_delegating_and_closes_db_only_after_exit(self):
        event = threading.Event()
        runtime = SimpleNamespace(stop_event=event, close=Mock())
        basic = SimpleNamespace(close=Mock())
        worker = SimpleNamespace(wsgi=SimpleNamespace(extensions={"education": runtime, "practice_store": basic}), handle_exit=Mock())
        previous = Mock(side_effect=lambda *_args: self.assertTrue(event.is_set()))
        with patch.object(signal, "getsignal", return_value=previous), patch.object(signal, "signal") as install:
            self.config.post_worker_init(worker)
            installed_signal, handler = install.call_args.args
        self.assertEqual(installed_signal, signal.SIGTERM)
        handler(signal.SIGTERM, None)
        previous.assert_called_once_with(signal.SIGTERM, None)
        worker.handle_exit.assert_not_called()
        runtime.close.assert_not_called()
        basic.close.assert_not_called()
        self.config.worker_exit(None, worker)
        runtime.close.assert_called_once()
        basic.close.assert_called_once()

    def test_sigterm_without_education_still_preserves_the_gunicorn_handler(self):
        previous = Mock()
        worker = SimpleNamespace(wsgi=SimpleNamespace(extensions={}), handle_exit=Mock())
        with patch.object(signal, "getsignal", return_value=previous), patch.object(signal, "signal") as install:
            self.config.post_worker_init(worker)
            handler = install.call_args.args[1]
        handler(signal.SIGTERM, None)
        previous.assert_called_once_with(signal.SIGTERM, None)

    def test_failed_worker_boot_does_not_import_or_construct_another_app(self):
        self.config.worker_exit(None, SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
