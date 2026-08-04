"""
create_user.py — Create a new user account from the command line.

Usage:
    python create_user.py <username> <password> [--admin] [--question Q] [--answer A]

Examples:
    python create_user.py operator1 mypassword123
    python create_user.py admin2 secret456 --admin
    python create_user.py joe pass789 --question "Mi az első autód?" --answer "Ford"
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password, hash_answer


def main():
    parser = argparse.ArgumentParser(description="Create a new user account")
    parser.add_argument("username", help="Username (min 3 characters)")
    parser.add_argument("password", help="Password (min 6 characters)")
    parser.add_argument("--admin", action="store_true", help="Create as admin")
    parser.add_argument("--question", "-q", help="Security question")
    parser.add_argument("--answer", "-a", help="Security answer")
    args = parser.parse_args()

    if len(args.username) < 3:
        print("ERROR: Username must be at least 3 characters")
        sys.exit(1)

    if len(args.password) < 6:
        print("ERROR: Password must be at least 6 characters")
        sys.exit(1)

    role = "admin" if args.admin else "user"

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == args.username).first()
        if existing:
            print(f"ERROR: User '{args.username}' already exists")
            sys.exit(1)

        user = User(
            username=args.username,
            password_hash=hash_password(args.password),
            role=role,
        )

        if args.question and args.answer:
            user.security_question = args.question
            user.security_answer_hash = hash_answer(args.answer)

        db.add(user)
        db.commit()

        print(f"[OK] User created: {user.username} (role: {user.role})")
        if args.question:
            print(f"  Security question: {args.question}")
        print(f"  ID: {user.id}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
