from pathlib import Path
from datetime import datetime, date
from dateutil.relativedelta import relativedelta


def list_old_files(folder: Path, age_in_days: int = 10) -> list[Path]:
    """Return a list of files in 'folder' whose modification date is older than 'age_in_days' days."""

    if not folder.exists():
        return []

    delete_threshold: date = datetime.now().date() + relativedelta(days=-age_in_days)

    old_files: list[Path] = []
    for file in folder.iterdir():
        modification_date = datetime.fromtimestamp(file.stat().st_mtime).date()
        if modification_date < delete_threshold:
            old_files.append(file)

    return old_files


def delete_files(files: list[Path]) -> None:
    """Delete files from a list of files."""
    for file in files:
        if file.is_dir():
            continue
        file.unlink(missing_ok=True)


def day_after_most_recent(files: list[Path]) -> datetime:
    """Return day after most recent modification for a list of files which can't be empty"""
    if not files:
        raise ValueError("files can't be an empty list")
    return max([datetime.fromtimestamp(file.stat().st_mtime) for file in files]) + relativedelta(days=1)


if __name__ == "__main__":
    extract_folder: Path = Path().cwd()  # / "TestFolder"

    files = list_old_files(extract_folder)  # liste des fichiers à supprimer
    files_nb = len(files)
    if files_nb:
        files_size = round(sum([size.stat().st_size for size in files]) / 1024**2, 2)
        files_date = day_after_most_recent(files)

        answer = input(
            "=" * 100
            + "\n"
            + f"Dans le dossier du cache temporaire des extractions il existe {files_nb} fichiers "
            + f"datant d'avant le {files_date.strftime('%d/%m/%Y')}.\n"
            + f"Ils vont être supprimés pour libérer {files_size} Mo d'espace disque.\n"
            + "Vous pouvez choisir d'annuler la suppression mais ce message reviendra à chaque ouverture.\n"
            + "Si certains des fichiers doivent être conservés merci de les changer de répertoire.\n"
            + "=" * 100
            + "\n"
            + "Suppression des fichiers ? (Y pour oui, n pour non) : "
        )

        if answer == "Y":
            delete_files(files)
