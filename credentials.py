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


def pwd_change(new_pwd: str):
    crypt_to_file(KEY_FILE, KEY, new_pwd)


def pwd_get() -> str:
    pwd = decrypt_from_file(KEY_FILE, KEY)
    return pwd.decode("UTF-8")


if __name__ == "__main__":
    if not "" == (new_pwd := input("Changer mot de passe (vide pour non) : ")):
        pwd_change(new_pwd)

    decrypted_text = pwd_get()
    print(f"decrypted : {decrypted_text}")
