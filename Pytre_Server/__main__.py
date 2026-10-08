import argparse
import getpass
import logging
from pathlib import Path

import uvicorn

from Pytre_Core.users import hash_password
from Pytre_Core.utils import log as _log

from Pytre_Server.app import create_app
from Pytre_Server.logs import setup_file_logging, setup_terminal_logging
from Pytre_Server.master_key import (
    get_master_key_interactive,
    show_master_key_interactive,
    set_master_key_interactive,
    delete_master_key_interactive,
)


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def create_parser() -> argparse.ArgumentParser:
    """Create and return an argument parser"""
    parser = argparse.ArgumentParser(description="Pytre Server")

    parser.add_argument("--host", default="127.0.0.1", help="Address to bind the server to (0.0.0.0 for any)")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the server on (default %(default)s)")
    parser.add_argument("--chunk-size", type=int, default=2000, help="Rows to stream to clients per NDJSON chunk")
    parser.add_argument("--max-concurrency-per-user", type=int, default=4, help="Max concurrent requests per user")
    parser.add_argument(
        "--heartbeat-secs",
        type=int,
        default=30,
        help="Seconds between keep alive pings while a query executes, 0 disables (default %(default)s)",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="File to log messages to (disabled by default)")
    parser.add_argument(
        "--forwarded-allow-ips",
        default=None,
        help="Comma-separated IPs of reverse proxies whose X-Forwarded-* headers are trusted (default: uvicorn's, "
        "i.e. $FORWARDED_ALLOW_IPS or 127.0.0.1). Never use '*' unless the server is reachable only through the proxy.",
    )

    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--hash-password", action="store_true", help="Prompt for a password and print its hash")
    action_group.add_argument("--show-master-key", action="store_true", help="Print stored master password")
    action_group.add_argument("--set-master-key", action="store_true", help="Prompt for a new master password")
    action_group.add_argument("--delete-master-key", action="store_true", help="Delete stored master password")

    return parser


def hash_password_interactive() -> None:
    """Prompt (masked, twice for confirmation) for a password and print its Argon2 hash."""
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match, cancelling...")
        return

    print(hash_password(password))


def process_master_key_args(args: argparse.Namespace) -> bool:
    """Process any master-key arg. Return True if any."""
    if args.show_master_key:
        show_master_key_interactive()
        return True

    if args.set_master_key:
        set_master_key_interactive()
        return True

    if args.delete_master_key:
        delete_master_key_interactive()
        return True

    return False


def start_server(args: argparse.Namespace):
    """Retrieve master_key, prompting user if not stored, and start the server"""

    master_key: str | None = get_master_key_interactive()
    if master_key is None:
        print("Startup cancelled: no master password configured.")
        return

    if args.forwarded_allow_ips and "*" in args.forwarded_allow_ips:
        log(
            "--forwarded-allow-ips '*': any client reaching this server directly can forge X-Forwarded-For "
            "(and pass as loopback/another IP) - make sure it is only reachable through the proxy",
            logging.WARNING,
        )

    uvicorn.run(
        create_app(
            master_key=master_key,
            chunk_size=args.chunk_size,
            max_concurrency_per_user=args.max_concurrency_per_user,
            heartbeat_secs=args.heartbeat_secs,
        ),
        host=args.host,
        port=args.port,
        forwarded_allow_ips=args.forwarded_allow_ips,
    )


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.hash_password:
        hash_password_interactive()
        return
    if process_master_key_args(args):  # return True if any
        return

    setup_terminal_logging(level=logging.INFO)
    if args.log_file is not None:
        setup_file_logging(args.log_file, level=logging.INFO)

    start_server(args)


if __name__ == "__main__":
    main()
