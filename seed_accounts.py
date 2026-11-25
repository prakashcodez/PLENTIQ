"""Create the five initial Plentiq demo accounts without storing plain passwords."""

from getpass import getpass

from werkzeug.security import generate_password_hash

from db import get_db_connection


ACCOUNTS = (
    {"name": "Prakash", "email": "prakashcodez@gmail.com", "role": "admin"},
    {"name": "Cosmic", "email": "cosmic123@gmail.com", "role": "donor"},
    {"name": "Nithil", "email": "cosmic456@gmail.com", "role": "donor"},
    {"name": "Robert", "email": "nithil123@gmail.com", "role": "acceptor"},
    {"name": "Prajit", "email": "nithil456@gmail.com", "role": "acceptor"},
)


def collect_password_hashes():
    """Prompt locally; never echo or save entered passwords as plain text."""
    hashes = {}
    for account in ACCOUNTS:
        while True:
            password = getpass(f"Set password for {account['email']}: ")
            confirmation = getpass("Confirm password: ")
            if password != confirmation:
                print("Passwords did not match. Try again.")
                continue
            if len(password) < 8:
                print("Use at least 8 characters. Try again.")
                continue
            hashes[account["email"]] = generate_password_hash(password)
            break
    return hashes


def seed_accounts():
    password_hashes = collect_password_hashes()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        emails = tuple(account["email"] for account in ACCOUNTS)
        placeholders = ", ".join("%s" for _ in emails)
        cursor.execute(
            f"SELECT email FROM users WHERE email IN ({placeholders})",
            emails,
        )
        existing = [row[0] for row in cursor.fetchall()]
        if existing:
            raise RuntimeError(
                "Some account emails already exist. No accounts were created; "
                "check the users table before retrying."
            )

        cursor.execute("SELECT role_id, role_code FROM roles")
        role_ids = {role_code: role_id for role_id, role_code in cursor.fetchall()}
        missing_roles = sorted(
            {account["role"] for account in ACCOUNTS} - set(role_ids)
        )
        if missing_roles:
            raise RuntimeError(
                "Missing role rows: " + ", ".join(missing_roles)
            )

        for account in ACCOUNTS:
            cursor.execute(
                """INSERT INTO users
                   (role_id, full_name, email, password_hash, account_status)
                   VALUES (%s, %s, %s, %s, 'active')""",
                (
                    role_ids[account["role"]],
                    account["name"],
                    account["email"],
                    password_hashes[account["email"]],
                ),
            )
            user_id = cursor.lastrowid

            if account["role"] == "donor":
                cursor.execute(
                    "INSERT INTO donor_profiles (user_id) VALUES (%s)",
                    (user_id,),
                )
            elif account["role"] == "acceptor":
                cursor.execute(
                    "INSERT INTO acceptor_profiles (user_id) VALUES (%s)",
                    (user_id,),
                )

        connection.commit()
        print("Created 5 accounts with password hashes; no plain passwords were stored.")
    except Exception as exc:
        if connection is not None and connection.is_connected():
            connection.rollback()
        # Do not print SQL parameters or password-related data.
        if isinstance(exc, RuntimeError):
            print(f"Account setup stopped: {exc}")
        else:
            error_code = getattr(exc, "errno", None)
            suffix = f" (MySQL error {error_code})" if error_code else ""
            print(f"Account setup failed{suffix}; no changes were committed.")
        raise SystemExit(1)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    seed_accounts()
