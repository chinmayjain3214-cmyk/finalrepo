"""Sign-in tests: attempt limits, input checks and no account enumeration.

No database is needed: the user lookup and the audit log are replaced.
"""

import unittest
from unittest import mock

import auth


class _FakeCursor:
    def __init__(self, users):
        self._users = users
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _sql, params):
        self._row = self._users.get(params[0])

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, users):
        self._users = users

    def cursor(self):
        return _FakeCursor(self._users)

    def close(self):
        pass


class LoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.users = {
            "finance1": (
                "finance1",
                "Demo Finance",
                "finance_officer",
                auth.hash_password("correct-horse"),
            ),
        }

    def setUp(self):
        auth.reset_login_attempts()
        self.logged = []
        self.clock = [1000.0]

        patches = [
            mock.patch.object(
                auth, "get_connection", lambda: _FakeConnection(self.users)
            ),
            mock.patch.object(
                auth, "save_log", lambda step, detail: self.logged.append((step, detail))
            ),
            mock.patch.object(auth.time, "monotonic", lambda: self.clock[0]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_correct_password_signs_in(self):
        outcome = auth.login("finance1", "correct-horse")
        self.assertEqual(outcome["status"], "ok")
        self.assertEqual(outcome["user"]["role"], "finance_officer")
        self.assertIn("auth:login", [step for step, _ in self.logged])

    def test_username_is_trimmed_and_case_insensitive(self):
        self.assertEqual(auth.login("  Finance1 ", "correct-horse")["status"], "ok")

    def test_empty_fields_are_not_counted_as_attempts(self):
        for _ in range(auth.MAX_FAILED_ATTEMPTS + 2):
            self.assertEqual(auth.login("finance1", "")["status"], "missing")
            self.assertEqual(auth.login("", "anything")["status"], "missing")
        self.assertEqual(auth.login("finance1", "correct-horse")["status"], "ok")

    def test_wrong_password_reports_attempts_left(self):
        outcome = auth.login("finance1", "nope")
        self.assertEqual(outcome["status"], "invalid")
        self.assertEqual(outcome["attempts_left"], auth.MAX_FAILED_ATTEMPTS - 1)

    def test_lockout_after_repeated_failures(self):
        for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
            self.assertEqual(auth.login("finance1", "nope")["status"], "invalid")

        outcome = auth.login("finance1", "nope")
        self.assertEqual(outcome["status"], "locked")
        self.assertEqual(outcome["retry_after"], auth.LOCKOUT_SECONDS)
        self.assertIn("auth:lockout", [step for step, _ in self.logged])

        # Even the correct password is refused while the pause is active.
        self.clock[0] += 30
        outcome = auth.login("finance1", "correct-horse")
        self.assertEqual(outcome["status"], "locked")
        self.assertEqual(outcome["retry_after"], auth.LOCKOUT_SECONDS - 30)

    def test_lockout_ends_and_counter_starts_fresh(self):
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            auth.login("finance1", "nope")

        self.clock[0] += auth.LOCKOUT_SECONDS + 1
        outcome = auth.login("finance1", "nope")
        self.assertEqual(outcome["status"], "invalid")
        self.assertEqual(outcome["attempts_left"], auth.MAX_FAILED_ATTEMPTS - 1)
        self.assertEqual(auth.login("finance1", "correct-horse")["status"], "ok")

    def test_success_resets_the_counter(self):
        for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
            auth.login("finance1", "nope")
        self.assertEqual(auth.login("finance1", "correct-horse")["status"], "ok")
        outcome = auth.login("finance1", "nope")
        self.assertEqual(outcome["attempts_left"], auth.MAX_FAILED_ATTEMPTS - 1)

    def test_old_failures_expire(self):
        for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
            auth.login("finance1", "nope")
        self.clock[0] += auth.FAILURE_WINDOW_SECONDS + 1
        outcome = auth.login("finance1", "nope")
        self.assertEqual(outcome["attempts_left"], auth.MAX_FAILED_ATTEMPTS - 1)

    def test_one_username_does_not_lock_another(self):
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            auth.login("someone-else", "nope")
        self.assertEqual(auth.login("finance1", "correct-horse")["status"], "ok")

    def test_unknown_username_behaves_like_a_known_one(self):
        with mock.patch.object(
            auth, "verify_password", wraps=auth.verify_password
        ) as verify:
            known = auth.login("finance1", "nope")
            unknown = auth.login("ghost", "nope")

        self.assertEqual(known, unknown)
        # A password check runs for both, so timing does not give it away.
        self.assertEqual(verify.call_count, 2)

        for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
            outcome = auth.login("ghost", "nope")
        self.assertEqual(outcome["status"], "locked")

    def test_over_long_password_is_rejected_without_hashing(self):
        with mock.patch.object(auth, "verify_password") as verify:
            outcome = auth.login("finance1", "x" * (auth.MAX_PASSWORD_LENGTH + 1))
        self.assertEqual(outcome["status"], "invalid")
        verify.assert_not_called()

    def test_account_with_unknown_role_cannot_sign_in(self):
        users = dict(self.users)
        users["odd"] = ("odd", "Odd Role", "superuser", auth.hash_password("pw"))
        with mock.patch.object(auth, "get_connection", lambda: _FakeConnection(users)):
            self.assertEqual(auth.login("odd", "pw")["status"], "invalid")

    def test_authenticate_keeps_its_original_contract(self):
        self.assertEqual(
            auth.authenticate("finance1", "correct-horse"),
            {"username": "finance1", "full_name": "Demo Finance", "role": "finance_officer"},
        )
        self.assertIsNone(auth.authenticate("finance1", "nope"))


if __name__ == "__main__":
    unittest.main()
