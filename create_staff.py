#!/usr/bin/env python3
"""Create a staff login. Usage: python create_staff.py username password"""

import sys

import auth
import database as db


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python create_staff.py <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    db.init_db()
    if db.get_staff_by_username(username):
        print("User already exists: {0}".format(username))
        sys.exit(1)

    user_id = db.create_staff_user(
        username, auth.hash_password(password), display_name=username
    )
    print("Staff user created (id={0}): {1}".format(user_id, username))


if __name__ == "__main__":
    main()
