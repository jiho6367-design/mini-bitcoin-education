"""A real process exit must preserve archives without reviving classroom authority."""

import unittest

from persistence_probe import local_restart


class EducationProcessRestartTests(unittest.TestCase):
    def test_owner_archive_keys_and_classroom_survive_a_complete_process_restart(self):
        report = local_restart()
        self.assertNotEqual(report["before_pid"], report["after_pid"])
        for check in ("complete_process_restart", "owner_cookie_retained",
                      "basic_practice_restored",
                      "personal_archive_and_key_restored", "other_browser_denied",
                      "classroom_archive_restored", "classroom_new_authority_only",
                      "http_and_websocket_exercised", "education_probe_records_cleaned",
                      "basic_probe_record_retained", "state_file_roundtrip"):
            self.assertTrue(report[check], check)
        self.assertFalse(report["production_gunicorn_verified"])


if __name__ == "__main__":
    unittest.main()
