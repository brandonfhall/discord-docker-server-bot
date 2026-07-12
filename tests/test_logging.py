import logging
import sys
import unittest


class TestRedactingFilter(unittest.TestCase):
    """Tests for the log-level token redaction filter applied at startup."""

    def setUp(self):
        from src.logging_config import RedactingFilter

        self._RedactingFilter = RedactingFilter

    def _make_record(self, msg, args=()):
        return logging.LogRecord("test", logging.INFO, "", 0, msg, args, None)

    def test_redacts_token_in_message(self):
        f = self._RedactingFilter(["supersecret"])
        record = self._make_record("auth token supersecret leaked")
        f.filter(record)
        self.assertNotIn("supersecret", record.msg)
        self.assertIn("[REDACTED]", record.msg)

    def test_redacts_token_in_formatted_args(self):
        """Token appearing via %-style formatting is redacted after expansion."""
        f = self._RedactingFilter(["supersecret"])
        record = self._make_record("token is %s", ("supersecret",))
        f.filter(record)
        self.assertNotIn("supersecret", record.msg)
        self.assertEqual(record.args, ())

    def test_redacts_multiple_tokens(self):
        f = self._RedactingFilter(["tok1", "tok2"])
        record = self._make_record("tok1 and tok2 both present")
        f.filter(record)
        self.assertNotIn("tok1", record.msg)
        self.assertNotIn("tok2", record.msg)

    def test_skips_empty_and_none_tokens(self):
        """Empty/None entries in token list must not raise errors."""
        f = self._RedactingFilter(["", None, "realtoken"])
        record = self._make_record("realtoken here")
        f.filter(record)
        self.assertNotIn("realtoken", record.msg)

    def test_preserves_clean_message(self):
        f = self._RedactingFilter(["secrettoken"])
        record = self._make_record("normal log message")
        f.filter(record)
        self.assertEqual(record.msg, "normal log message")

    def test_always_returns_true(self):
        f = self._RedactingFilter(["token"])
        record = self._make_record("some message")
        self.assertTrue(f.filter(record))

    def test_empty_token_list_is_noop(self):
        f = self._RedactingFilter([])
        record = self._make_record("message stays unchanged")
        f.filter(record)
        self.assertEqual(record.msg, "message stays unchanged")

    def test_redacts_token_in_exception_traceback(self):
        """L2: a token appearing in an exception's str() (exc_info), not just the
        log message itself, must also be redacted before any handler formats it."""
        f = self._RedactingFilter(["supersecret"])
        try:
            raise RuntimeError("token supersecret leaked in traceback")
        except RuntimeError:
            record = self._make_record("something failed")
            record.exc_info = sys.exc_info()

        f.filter(record)

        self.assertIsNone(record.exc_info)
        self.assertNotIn("supersecret", record.exc_text)
        self.assertIn("[REDACTED]", record.exc_text)
