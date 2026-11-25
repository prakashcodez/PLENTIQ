"""Read-only check that the configured Plentiq MySQL connection works."""

import sys

from db import get_db_connection


def main():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE()")
        database_name = cursor.fetchone()[0]
        print(f"MySQL connection successful: {database_name}")
    except Exception as exc:
        # Do not print connection settings or passwords in diagnostic output.
        if isinstance(exc, RuntimeError):
            print(f"Configuration error: {exc}")
        else:
            error_code = getattr(exc, "errno", None)
            suffix = f" (MySQL error {error_code})" if error_code else ""
            print(f"MySQL connection failed{suffix}. Check the local .env settings.")
        return 1
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
