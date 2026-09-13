import tkinter as tk

from Pytre_Core.remotes import Remote, RemoteAddress

from Pytre_Client.LoginDialog import LoginDialog


def prompt_remote_credentials(
    remote: Remote, parent: tk.Tk | tk.Toplevel | None = None, prompt: str | None = None, save: bool = True
) -> bool:
    """Prompts for corrected username/password for `remote` (pre-filled with its current values),
    applies and if `save` is True persists them on confirmation.
    Returns whether the modification succeeded if user confirmed. If not return False"""

    remote_id = remote.address.description or remote.id

    result = LoginDialog.ask(
        title="Authentification requise",
        prompt=prompt or f"Connexion impossible à {remote_id}\nMerci de vous connecter :",
        login=remote.address.username,
        password=remote.address.password,
        parent=parent,
    )

    if result is None:
        return False

    remote.address.username, remote.address.password = result
    if save:
        remote.save()

    return True


if __name__ == "__main__":
    remote_address = RemoteAddress("http://test", 8000, "user_test", password="dummy_pwd")
    remote = Remote(None, remote_address)
    success = prompt_remote_credentials(remote, save=False)
    print(success)
