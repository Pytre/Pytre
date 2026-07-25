import logging
import os
import subprocess
from cryptography.fernet import Fernet

from Pytre_Core.keyring import KeyringCatalog
from Pytre_Core.utils import log as _log

ENCODING = "utf-8"
MASTER_KEY: KeyringCatalog = KeyringCatalog.SERVER_MASTER_KEY


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def get_master_key() -> str | None:
    """Returns the master password stored in the system keyring, or None if none is registered yet"""
    return MASTER_KEY.get_key(create_missing=False)


def set_master_key(value: str) -> None:
    """Stores `value` as the master password in the system keyring."""
    MASTER_KEY.set_key(value)


def delete_master_key() -> None:
    """Deletes the master password stored in the system keyring."""
    MASTER_KEY.delete_key()


# ------------------------------------------------------------------------------------------
# Functions to interact with user inside the terminal
# ------------------------------------------------------------------------------------------
def clear_terminal(confirm: bool = True):
    """When `confirm` is true then ask for user input before clearing the terminal"""
    if confirm:
        input("Terminal will be cleared, press any key to continue...")

    if os.name == "nt":
        subprocess.run(["cmd", "/c", "cls"])
    else:
        subprocess.run(["clear"])


def get_master_key_interactive() -> str | None:
    """Return the master password if already stored. If not then prompts, using the terminal,
    for a master password. Stores it and returns the chosen value or None if the admin cancelled."""
    value: str | None = get_master_key()
    if value:
        return value

    print("No master password saved on the system keyring. Choose what to do:")
    print("1) Use a random password (will be printed) - Only use if no previous base exists")
    print("2) Enter password to be used")
    print("3) Quit")
    choice = input("Choice: ").strip()

    if choice == "1":
        value = Fernet.generate_key().decode(ENCODING)
        print(f"Master password set to: {value}")
    elif choice == "2":
        value = input("Master password: ").strip()

    if not value:
        print("No valid choice or empty password...")
        return None

    set_master_key(value)
    clear_terminal()

    return value


def show_master_key_interactive() -> None:
    """Print the master password in the terminal"""
    value = get_master_key()
    if value is None:
        print("No master password saved on the system keyring.")
    else:
        print(value)


def set_master_key_interactive() -> None:
    """Set a new master password using the terminal"""

    value = input("New master password: ").strip()
    if not value:
        print("No input, cancelling...")
        return

    if not input("Confirm master password (y/N): ").lower() == "y":
        print("Not confirmed, cancelling...")
        return

    set_master_key(value)
    print("Master password saved on the system keyring.")


def delete_master_key_interactive() -> None:
    """Delete, after confirmation, the stored master password using the terminal."""
    if get_master_key() is None:
        print("No master password saved on the system keyring.")
        return

    if not input("Delete the stored master password? (y/N): ").lower() == "y":
        print("Not confirmed, cancelling...")
        return

    delete_master_key()
    print("Master password deleted from the system keyring.")
