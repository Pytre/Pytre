from tkinter import messagebox, filedialog
from pathlib import Path

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from user_prefs import UserPrefsEnum, UserPrefs


def save_as(parent, src_file: Path) -> Path:
    if not src_file:
        return None
    elif not Path(src_file).exists():
        messagebox.showerror("Erreur", "Le fichier source n'existe pas", parent=parent)
        return None

    user_prefs = UserPrefs()
    save_as_folder = UserPrefsEnum.save_as_folder

    filetypes = [("Csv Files", "*.csv"), ("Tous", "*.*")]
    initialdir = user_prefs.get(save_as_folder)
    dest = filedialog.asksaveasfilename(
        title="Enregistrer sous",
        defaultextension=".csv",
        filetypes=filetypes,
        initialdir=initialdir,
        initialfile=Path(src_file).name,
        parent=parent,
    )

    if dest:
        Path(dest).write_bytes(Path(src_file).read_bytes())
        user_prefs.set(save_as_folder, Path(dest).parent)
        return Path(dest)

    return None


if __name__ == "__main__":
    file = [file for file in list(Path(".").glob("*")) if file.is_file()][0]
    save_as(None, file)
