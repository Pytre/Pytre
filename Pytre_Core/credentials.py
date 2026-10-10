from pathlib import Path
from cryptography.fernet import Fernet


KEY_FILE = Path().cwd() / "Pytre.key"


def _secret_key() -> bytes:
    """Key used to encrypt KEY_FILE - only needed with a KeePass database (hybrid client, server).
    Raises RuntimeError if Pytre_Core/credentials_secrets.py is missing."""
    try:
        from Pytre_Core.credentials_secrets import KEY
    except ImportError as e:
        raise RuntimeError(
            "Pytre_Core/credentials_secrets.py is missing: copy it from templates/ and change its key"
        ) from e

    return KEY


def create_key_and_export(filename: Path) -> None:
    key = Fernet.generate_key()
    with open(filename, "wb") as key_file:
        key_file.write(key)


def retrieve_key(filename: Path) -> bytes:
    with open(filename, "rb") as key_file:
        key = key_file.read()

    return key


def crypt_to_file(filename: Path, key: bytes, txt: str):
    cipher = Fernet(key)
    text_bytes = txt.encode()
    crypted_text = cipher.encrypt(text_bytes)

    with open(filename, "wb") as file:
        file.write(crypted_text)


def decrypt_from_file(filename: Path, key: bytes) -> str:
    with open(filename, "rb") as file:
        txt = file.read()

    cipher = Fernet(key)
    decrypted_text = cipher.decrypt(txt)

    return decrypted_text.decode("UTF-8")


def crypted_file_pwd_change(new_pwd: str):
    """Records `new_pwd` as the settings database password in KEY_FILE"""
    crypt_to_file(KEY_FILE, _secret_key(), new_pwd)


def crypted_file_pwd_get() -> str:
    """Settings database password recorded in KEY_FILE, empty if none.
    Older files hold several passwords, one per line: the first one is the current password."""
    if not KEY_FILE.exists():
        return ""

    pwd_from_file = decrypt_from_file(KEY_FILE, _secret_key())
    return pwd_from_file.split("\n")[0] if pwd_from_file else ""


if __name__ == "__main__":
    if not "" == (new_pwd := input("Changer mot de passe (vide pour non) : ")):
        crypted_file_pwd_change(new_pwd)

    pwd = crypted_file_pwd_get()
    print(f"{'=' * 50}\nMot de passe courant : {pwd}")
