"""
manage_users.py – Command-line account management (run on the server, in the platform's Python environment).

Run from the backend folder (same .env as the backend):
    python tools/manage_users.py create-admin <username> [--email a@b.no]     # first administrator (asks for a password)
    python tools/manage_users.py import-json [path/allowed_users.json]       # import legacy accounts (passwords get hashed)
    python tools/manage_users.py set-password <username>                    # reset a password
    python tools/manage_users.py set-role <username> admin|user|viewer
    python tools/manage_users.py set-status <username> active|disabled|pending
    python tools/manage_users.py list
"""
import argparse, getpass, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "app"), os.path.join(HERE, "..")]
from backend.app.config import ALLOWED_USERS
from backend.app.services import auth_store as store, constants

def ask_password(username: str) -> str:
    while True:
        p1 = getpass.getpass(f"New password for {username}: ")
        p2 = getpass.getpass("Repeat: ")
        if p1 != p2: print("Passwords do not match."); continue
        try: store.check_password(p1, username); return p1
        except store.AuthError as e: print(e)

def main():
    ap = argparse.ArgumentParser(description="Fjord Platform user management")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin"); a.add_argument("username"); a.add_argument("--email")
    a = sub.add_parser("import-json"); a.add_argument("path", nargs="?", default=ALLOWED_USERS)
    a.add_argument("--admin", action="append", default=[], help="legacy account that becomes admin (repeatable)")
    a = sub.add_parser("set-password"); a.add_argument("username")
    a = sub.add_parser("set-role"); a.add_argument("username"); a.add_argument("role", choices=constants.ROLES)
    a = sub.add_parser("set-status"); a.add_argument("username"); a.add_argument("status", choices=constants.STATUSES)
    sub.add_parser("list")
    args = ap.parse_args()
    store.init_db()
    try:
        if args.cmd == "create-admin":
            u = store.create_user(
                args.username, email=args.email, password=ask_password(args.username), role="admin", status="active", allow_reserved=True
            )
            store.audit("user_created_cli", actor="cli", target=u.username)
            print(f"Administrator '{u.username}' created.")
        elif args.cmd == "import-json":
            with open(args.path, encoding="utf-8-sig") as f: old = json.load(f)
            done, skipped = 0, []
            for name, pw in old.items():
                try:
                    if store.get_user(name): skipped.append(f"{name} (exists)"); continue
                    # Legacy passwords may be shorter than the new policy -> hashed as they are; users should change them
                    u = store.create_user(
                        name, role="admin" if name in args.admin else "user", status="active", allow_reserved=True
                    )
                    with store.db() as con:
                        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (store._ph.hash(pw), u.id))
                    done += 1
                except store.AuthError as e: skipped.append(f"{name} ({e})")
            print(f"Imported {done} account(s).")
            if skipped: print("Skipped:", ", ".join(skipped))
            print("Next: delete or move allowed_users.json – it contains plain-text passwords.")
        elif args.cmd == "set-password":
            store.set_password(args.username, ask_password(args.username)); print("Password updated (all sessions signed out).")
        elif args.cmd == "set-role":
            store.update_user(args.username, role=args.role); print("Role updated.")
        elif args.cmd == "set-status":
            store.update_user(args.username, status=args.status); print("Status updated.")
        elif args.cmd == "list":
            for u in store.list_users():
                print(f"{u['username']:<20} {u['role']:<7} {u['status']:<9} {u['email'] or ''}")
    except store.AuthError as e:
        sys.exit(f"Error: {e}")

if __name__ == "__main__":
    main()
