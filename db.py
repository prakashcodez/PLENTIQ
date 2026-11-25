"""MySQL connection configuration for the Plentiq application."""

import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv


# Load only this project's local environment file. It is ignored by Git.
load_dotenv(Path(__file__).resolve().parent / ".env")


def get_db_connection():
    """Return a MySQL connection using the root project's .env settings."""
    required = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing required .env settings: " + ", ".join(missing)
        )

    try:
        port = int(os.getenv("MYSQL_PORT", "3306"))
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT must be a number") from exc

    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=port,
        database=os.environ["MYSQL_DATABASE"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        connection_timeout=5,
        charset="utf8mb4",
    )
