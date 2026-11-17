import os
import unittest
from unittest.mock import patch

from app import database


class DatabaseSslTests(unittest.TestCase):
    def test_sslmode_is_omitted_for_local_connections(self):
        with (
            patch.dict(os.environ, {"DB_SSLMODE": ""}, clear=False),
            patch.object(database.psycopg, "connect", return_value=object()) as connect,
        ):
            database.get_connection()
        self.assertNotIn("sslmode", connect.call_args.kwargs)

    def test_sslmode_is_passed_when_configured(self):
        with (
            patch.dict(os.environ, {"DB_SSLMODE": "require"}, clear=False),
            patch.object(database.psycopg, "connect", return_value=object()) as connect,
        ):
            database.get_connection()
        self.assertEqual(connect.call_args.kwargs["sslmode"], "require")


if __name__ == "__main__":
    unittest.main()
