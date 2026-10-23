import os

from dotenv import load_dotenv


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

db_password = os.environ.get("PLENTIQ_DB_PASSWORD")
if not db_password:
    raise RuntimeError(
        "PLENTIQ_DB_PASSWORD is required. Set it in the process environment or the root .env file."
    )

try:
    db_port = int(os.environ.get("PLENTIQ_DB_PORT", "3306"))
except ValueError as exc:
    raise RuntimeError("PLENTIQ_DB_PORT must be an integer.") from exc

DB_CONFIG = {
    "host": os.environ.get("PLENTIQ_DB_HOST", "localhost"),
    "user": os.environ.get("PLENTIQ_DB_USER", "root"),
    "password": db_password,
    "database": os.environ.get("PLENTIQ_DB_NAME", "plentiq_db"),
    "port": db_port,
}
