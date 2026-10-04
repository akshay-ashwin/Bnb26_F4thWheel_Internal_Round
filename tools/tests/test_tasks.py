"""Database tasks: only the parts that need no Docker (the rest is exercised by `fd test-api`)."""

from __future__ import annotations

import unittest

from fd import tasks


class DatabaseTaskGuards(unittest.TestCase):
    def test_test_database_name_ends_in_test(self) -> None:
        # api/tests/conftest.py refuses any other name; the task must never point elsewhere.
        self.assertTrue(tasks.TEST_DB.endswith("_test"))

    def test_app_password_must_be_plain_alphanumeric(self) -> None:
        # The password is written into an ALTER ROLE statement, so quotes must never get in.
        self.assertIsNotNone(tasks._SAFE_SECRET.fullmatch("0123456789abcdef" * 4))
        for bad in ("", "short", "has'quote" + "a" * 20, "semi;colon" + "a" * 20, "a" * 20 + " "):
            self.assertIsNone(tasks._SAFE_SECRET.fullmatch(bad), bad)

    def test_database_tasks_are_implemented(self) -> None:
        for task in ("migrate", "reset-db", "test-api"):
            self.assertNotIn(task, tasks.NOT_IMPLEMENTED)


if __name__ == "__main__":
    unittest.main()
