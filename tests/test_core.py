from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from PySide6.QtCore import QEvent
from PySide6.QtSql import QSqlQuery
from PySide6.QtWidgets import QApplication

import migrate
import paths
import release_metadata
import updater
import ui_settings
import user_profiles
from db_qt import close_db, open_db
from ui_courses import calculate_previous_course_gpa_metrics, list_current_course_completion_state
from ui_settings import (
    SET_GRADE_CALC_MODE,
    SET_GRADE_DISPLAY,
    SET_OPENAI_API_KEY,
    _import_backup_json,
    _set,
    _settings_table_snapshot,
    set_str,
)
from ui_tasks import import_tasks_from_excel, load_workbook
from version import APP_BUNDLE_IDENTIFIER, APP_BUNDLE_NAME, GITHUB_REPO_SLUG


APP = QApplication.instance() or QApplication([])


def execute(sql: str, values: tuple = ()) -> None:
    query = QSqlQuery()
    query.prepare(sql)
    for value in values:
        query.addBindValue(value)
    if not query.exec():
        raise AssertionError(query.lastError().text())


def scalar(sql: str):
    query = QSqlQuery()
    if not query.exec(sql):
        raise AssertionError(query.lastError().text())
    if not query.next():
        raise AssertionError(f"Query returned no rows: {sql}")
    return query.value(0)


class DatabaseBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        paths.set_active_data_paths(data_dir / "syllora.db", data_dir / "settings.ini")
        open_db()
        migrate.ensure_schema()

    def tearDown(self) -> None:
        close_db()
        self.temp_dir.cleanup()

    def test_database_safety_pragmas_are_enabled(self) -> None:
        self.assertEqual(int(scalar("PRAGMA foreign_keys")), 1)
        self.assertGreaterEqual(int(scalar("PRAGMA busy_timeout")), 5000)
        self.assertEqual(str(scalar("PRAGMA journal_mode")).lower(), "wal")

    def test_backup_snapshot_excludes_api_key(self) -> None:
        _set(SET_OPENAI_API_KEY, "test-secret-that-must-not-be-backed-up")
        _set("ordinary_setting", "kept")
        snapshot = _settings_table_snapshot()
        self.assertNotIn(SET_OPENAI_API_KEY, snapshot)
        self.assertEqual(snapshot["ordinary_setting"], "kept")

    def test_backup_restore_is_atomic_and_preserves_unknown_due_dates(self) -> None:
        backup_path = Path(self.temp_dir.name) / "backup.json"
        backup_path.write_text(
            json.dumps(
                {
                    "courses": [{"name": "Restored Course", "term": "fall", "academic_year_start": 2026}],
                    "tasks": [{"course": "Restored Course", "item": "Undated task", "due_datetime": ""}],
                    "previous_courses": [],
                    "settings": {
                        SET_OPENAI_API_KEY: "must-not-restore",
                        "ordinary_setting": "restored",
                    },
                    "app_qsettings": {"post_commit_preference": "restored"},
                }
            ),
            encoding="utf-8",
        )

        with patch.object(ui_settings, "_ensure_course", side_effect=RuntimeError("injected restore failure")):
            with self.assertRaisesRegex(RuntimeError, "injected restore failure"):
                _import_backup_json(str(backup_path))

        self.assertEqual(int(scalar("SELECT count(*) FROM courses")), 0)
        self.assertEqual(int(scalar("SELECT count(*) FROM settings")), 0)
        self.assertIsNone(ui_settings.app_qsettings().value("post_commit_preference"))

        _import_backup_json(str(backup_path))
        self.assertEqual(int(scalar("SELECT due_datetime IS NULL FROM tasks WHERE item='Undated task'")), 1)
        self.assertEqual(int(scalar(f"SELECT count(*) FROM settings WHERE key='{SET_OPENAI_API_KEY}'")), 0)
        self.assertEqual(scalar("SELECT value FROM settings WHERE key='ordinary_setting'"), "restored")
        self.assertEqual(ui_settings.app_qsettings().value("post_commit_preference"), "restored")

    def test_submitted_without_grade_can_count_as_zero(self) -> None:
        execute(
            "INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)",
            ("Test Course", "fall", 2026),
        )
        course_id = int(scalar("SELECT id FROM courses WHERE name='Test Course'"))
        execute(
            "INSERT INTO tasks(course_id, item, status, weight, grade) VALUES(?, ?, ?, ?, ?)",
            (course_id, "Graded", "graded", 50.0, 100.0),
        )
        execute(
            "INSERT INTO tasks(course_id, item, status, weight, grade) VALUES(?, ?, ?, ?, ?)",
            (course_id, "Submitted", "submitted", 50.0, None),
        )

        set_str(SET_GRADE_CALC_MODE, "graded_only")
        self.assertAlmostEqual(list_current_course_completion_state()[course_id]["final_percent"], 100.0)

        set_str(SET_GRADE_CALC_MODE, "submitted_as_zero")
        self.assertAlmostEqual(list_current_course_completion_state()[course_id]["final_percent"], 50.0)

    def test_previous_course_gpa_uses_selected_display_scale(self) -> None:
        rows = [
            {
                "id": 1,
                "course_name": "A Plus Course",
                "final_percent": 95.0,
                "final_letter": "",
                "credit_hours": 3.0,
                "term": "fall",
                "academic_year_start": 2025,
                "created_at": "2025-09-01",
            },
            {
                "id": 2,
                "course_name": "A Course",
                "final_percent": -1.0,
                "final_letter": "A",
                "credit_hours": 3.0,
                "term": "winter",
                "academic_year_start": 2025,
                "created_at": "2026-01-01",
            },
        ]

        set_str(SET_GRADE_DISPLAY, "gpa4.0")
        metrics_40 = calculate_previous_course_gpa_metrics(rows, 6)
        self.assertAlmostEqual(metrics_40["cumulative"]["gpa"], 4.0)
        self.assertAlmostEqual(metrics_40["last_x"]["gpa"], 4.0)
        self.assertEqual(metrics_40["cumulative"]["scale"], "4.0")

        set_str(SET_GRADE_DISPLAY, "gpa4.3")
        metrics_43 = calculate_previous_course_gpa_metrics(rows, 6)
        self.assertAlmostEqual(metrics_43["cumulative"]["gpa"], 4.15)
        self.assertAlmostEqual(metrics_43["last_x"]["gpa"], 4.15)
        self.assertEqual(metrics_43["cumulative"]["scale"], "4.3")

    @unittest.skipIf(load_workbook is None, "openpyxl is not installed")
    def test_excel_import_keeps_missing_due_date_unknown(self) -> None:
        from openpyxl import Workbook

        workbook_path = Path(self.temp_dir.name) / "tasks.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Tasks"
        sheet.append(["Course", "Item", "Due Date"])
        sheet.append(["Undated Course", "Research topic", None])
        workbook.save(workbook_path)

        _, tasks_added = import_tasks_from_excel(str(workbook_path))
        self.assertEqual(tasks_added, 1)
        self.assertEqual(int(scalar("SELECT due_datetime IS NULL FROM tasks WHERE item='Research topic'")), 1)
        self.assertEqual(scalar("SELECT due_status FROM tasks WHERE item='Research topic'"), "unknown")

    def test_schema_creation_rolls_back_on_failure(self) -> None:
        close_db()
        data_dir = Path(self.temp_dir.name)
        failing_db = data_dir / "failing.db"
        paths.set_active_data_paths(failing_db, data_dir / "failing.ini")
        open_db()

        original_exec = migrate._exec
        call_count = 0

        def fail_after_first_statement(sql: str) -> None:
            nonlocal call_count
            original_exec(sql)
            call_count += 1
            if call_count == 2:
                raise RuntimeError("injected migration failure")

        with patch.object(migrate, "_exec", side_effect=fail_after_first_statement):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                migrate.ensure_schema()

        self.assertEqual(int(scalar("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('courses','previous_courses')")), 0)

    def test_legacy_task_rebuild_keeps_rows_with_foreign_keys_enabled(self) -> None:
        close_db()
        data_dir = Path(self.temp_dir.name)
        legacy_path = data_dir / "legacy.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(
                """
                CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY,
                    course_id INTEGER NOT NULL,
                    project_id INTEGER,
                    item TEXT NOT NULL,
                    component TEXT DEFAULT '',
                    due_datetime TEXT,
                    status TEXT NOT NULL DEFAULT 'not started',
                    weight REAL,
                    grade REAL,
                    ungraded INTEGER NOT NULL DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                );
                INSERT INTO courses(id, name) VALUES(1, 'Legacy Course');
                INSERT INTO tasks(id, course_id, project_id, item) VALUES(1, 1, 99, 'Legacy Task');
                """
            )

        paths.set_active_data_paths(legacy_path, data_dir / "legacy.ini")
        open_db()
        migrate.ensure_schema()

        columns = {str(row[1]) for row in sqlite3.connect(legacy_path).execute("PRAGMA table_info(tasks)")}
        self.assertNotIn("project_id", columns)
        self.assertIn("priority", columns)
        self.assertEqual(scalar("SELECT item FROM tasks WHERE id=1"), "Legacy Task")


class UpdaterSafetyTests(unittest.TestCase):
    def test_latest_release_manifest_is_preferred(self) -> None:
        self.assertEqual(updater.LATEST_JSON_URLS[0], updater.LATEST_RELEASE_MANIFEST_URL)
        self.assertEqual(
            updater.LATEST_RELEASE_MANIFEST_URL,
            f"https://github.com/{GITHUB_REPO_SLUG}/releases/latest/download/latest.json",
        )

    def test_manifest_requires_official_release_and_valid_checksum(self) -> None:
        base = {
            "latest_version": "99.0.0",
            "macos_zip_url": f"https://github.com/{GITHUB_REPO_SLUG}/releases/download/v99/Syllora.zip",
            "sha256": "a" * 64,
        }
        with patch.object(updater, "_load_json_from_url", return_value=dict(base)):
            self.assertTrue(updater.check_for_updates()["update_available"])

        outside = dict(base, macos_zip_url="https://example.com/Syllora.zip")
        with patch.object(updater, "_load_json_from_url", return_value=outside):
            with self.assertRaisesRegex(RuntimeError, "outside the official"):
                updater.check_for_updates()

        invalid_hash = dict(base, sha256="not-a-checksum")
        with patch.object(updater, "_load_json_from_url", return_value=invalid_hash):
            with self.assertRaisesRegex(RuntimeError, "invalid SHA-256"):
                updater.check_for_updates()

    def test_private_or_missing_manifest_has_actionable_error(self) -> None:
        not_found = urllib.error.HTTPError(
            updater.LATEST_RELEASE_MANIFEST_URL,
            404,
            "Not Found",
            None,
            None,
        )
        with patch.object(updater, "_load_json_from_url", side_effect=not_found):
            with self.assertRaisesRegex(RuntimeError, "repository may be private"):
                updater.check_for_updates()

    def test_installer_checks_bundle_identity_and_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_path = updater.write_update_installer_script(
                root / "Syllora.zip",
                root / APP_BUNDLE_NAME,
            )
            script = script_path.read_text(encoding="utf-8")
            self.assertIn(APP_BUNDLE_IDENTIFIER, script)
            self.assertIn("codesign --verify --deep --strict", script)
            self.assertIn('if ! wait_for_pid "$APP_PID"', script)
            self.assertIn('/bin/rm -rf "$RUNTIME_DIR"', script)
            syntax_check = subprocess.run(
                ["/bin/zsh", "-n", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(syntax_check.returncode, 0, syntax_check.stderr)


class ReleaseMetadataTests(unittest.TestCase):
    def test_automated_release_manifest_matches_github_asset(self) -> None:
        manifest = release_metadata.build_manifest(
            version="1.0.9.42.1",
            repository=GITHUB_REPO_SLUG,
            release_tag="auto-v1.0.9.42.1",
            asset_name="Syllora-1.0.9.42.1-macos.zip",
            sha256="A" * 64,
            release_notes=["Automated test release."],
        )
        self.assertEqual(manifest["latest_version"], "1.0.9.42.1")
        self.assertEqual(manifest["sha256"], "a" * 64)
        self.assertEqual(
            manifest["macos_zip_url"],
            "https://github.com/arnoreli53/Syllora/releases/download/"
            "auto-v1.0.9.42.1/Syllora-1.0.9.42.1-macos.zip",
        )

    def test_release_metadata_rejects_unsafe_values(self) -> None:
        common = {
            "version": "1.0.9",
            "repository": GITHUB_REPO_SLUG,
            "release_tag": "v1.0.9",
            "asset_name": "Syllora-1.0.9-macos.zip",
            "sha256": "a" * 64,
        }
        with self.assertRaises(ValueError):
            release_metadata.build_manifest(**dict(common, release_tag="../unsafe"))
        with self.assertRaises(ValueError):
            release_metadata.build_manifest(**dict(common, sha256="invalid"))


class UiSmokeTests(unittest.TestCase):
    def test_main_window_builds_at_a_laptop_friendly_size(self) -> None:
        import ui_main
        import ui_settings

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths.set_active_data_paths(root / "ui.db", root / "settings.ini")
            open_db()
            migrate.ensure_schema()
            try:
                with (
                    patch.object(ui_settings, "current_profile", return_value=None),
                    patch.object(ui_settings, "list_profiles", return_value=[]),
                    patch.object(ui_settings, "load_secret", return_value=""),
                    patch.object(ui_main, "current_profile", return_value=None),
                    patch.object(ui_main, "list_profiles", return_value=[]),
                ):
                    window = ui_main.MainWindow()
                    window.show()
                    APP.processEvents()
                    self.assertEqual(window.tab_widget.count(), 4)
                    self.assertLessEqual(window.minimumWidth(), 820)
                    self.assertLessEqual(window.minimumHeight(), 600)
                    screenshot_path = os.environ.get("SYLLORA_UI_SCREENSHOT", "").strip()
                    if screenshot_path:
                        requested_tab = os.environ.get("SYLLORA_UI_TAB", "").strip().lower()
                        for index in range(window.tab_widget.count()):
                            if window.tab_widget.tabText(index).lower() == requested_tab:
                                window.tab_widget.setCurrentIndex(index)
                                APP.processEvents()
                                break
                        self.assertTrue(window.grab().save(screenshot_path))
                    window._clear_pages()
                    window.setCentralWidget(None)
                    window.close()
                    window.deleteLater()
                    del window
                    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    APP.processEvents()
            finally:
                close_db()


class RegistryResilienceTests(unittest.TestCase):
    def test_registry_recovers_from_last_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "users.json"
            backup = root / "users.json.bak"
            first = {"version": 1, "current_user_id": "one", "profiles": [{"id": "one", "name": "One"}]}
            second = {"version": 1, "current_user_id": "two", "profiles": [{"id": "two", "name": "Two"}]}

            with (
                patch.object(paths, "APP_SUPPORT_DIR", root),
                patch.object(user_profiles, "REGISTRY_PATH", primary),
                patch.object(user_profiles, "REGISTRY_BACKUP_PATH", backup),
            ):
                user_profiles._write_registry(first)
                user_profiles._write_registry(second)
                primary.write_text("{broken", encoding="utf-8")
                recovered = user_profiles._read_registry()

            self.assertEqual(recovered["current_user_id"], "one")
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["current_user_id"], "one")


if __name__ == "__main__":
    unittest.main()
