from tkinter import messagebox, filedialog
from pathlib import Path

from Pytre_Core.user_prefs import PrefsEnum, UserPrefs


def save_as(user_prefs: UserPrefs, parent, src_file: Path | str) -> Path | None:
    if not src_file:
        return None
    elif not Path(src_file).exists():
        messagebox.showerror("Erreur", "Le fichier source n'existe pas", parent=parent)
        return None

    dest = filedialog.asksaveasfilename(
        title="Enregistrer sous",
        defaultextension=".csv",
        filetypes=[("Csv Files", "*.csv"), ("Tous", "*.*")],
        initialdir=user_prefs.get(PrefsEnum.save_as_folder),
        initialfile=Path(src_file).name,
        parent=parent,
    )

    if dest:
        Path(dest).write_bytes(Path(src_file).read_bytes())
        user_prefs.set(PrefsEnum.save_as_folder, Path(dest).parent)
        return Path(dest)

    return None


if __name__ == "__main__":
    file = [file for file in list(Path(".").glob("*")) if file.is_file()][0]
    user_prefs = UserPrefs()
    save_as(user_prefs, None, file)
