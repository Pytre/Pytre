from pathlib import Path
from cryptography.fernet import Fernet

from credentials_secrets import KEY


KEY_FILE = Path().cwd() / "Pytre.key"


def create_key_and_export(filename: Path) -> None:
    key = Fernet.generate_key()
    with open(filename, "wb") as key_file:
        key_file.write(key)


def retrieve_key(filename: Path) -> str:
    with open(filename, "r") as key_file:
        key = key_file.read()

    return key


def crypt_to_file(filename: Path, key: Fernet, txt: str):
    cipher = Fernet(key)
    txt = txt.encode()
    crypted_text = cipher.encrypt(txt)

    with open(filename, "wb") as file:
        file.write(crypted_text)


def decrypt_from_file(filename: Path, key: Fernet) -> str:
    with open(filename, "r") as file:
        txt = file.read()

    cipher = Fernet(key)
    decrypted_text = cipher.decrypt(txt)

    return decrypted_text


def crypted_file_pwd_change(new_pwd: str):
    pwd_history = crypted_file_pwd_history()

    if pwd_history:
        pwd_history.insert(0, new_pwd)
    else:
        pwd_history = [new_pwd]

    text_to_crypt = "\n".join(pwd_history[:25])
    crypt_to_file(KEY_FILE, KEY, text_to_crypt)


def crypted_file_pwd_history() -> list[str]:
    try:
        pwd = decrypt_from_file(KEY_FILE, KEY).decode("UTF-8")
        pwd_history = pwd.split("\n")
    except FileNotFoundError:
        pwd_history = []

    return pwd_history


def crypted_file_pwd_get() -> str:
    pwd_history = crypted_file_pwd_history()
    pwd_last = pwd_history[0] if pwd_history else ""
    return pwd_last


if __name__ == "__main__":
    if not "" == (new_pwd := input("Changer mot de passe (vide pour non) : ")):
        crypted_file_pwd_change(new_pwd)

    pwd_history = "\n".join(crypted_file_pwd_history())
    print(f"{'=' * 50}\nHistorique mots de passe :\n{pwd_history}")
    pwd = crypted_file_pwd_get()
    print(f"{'=' * 50}\nMot de passe courant : {pwd}")
