from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta

from logs import LOG_FILE


def old_files_list(folder: Path) -> list[Path]:
    white_list = (LOG_FILE,)

    if not folder.exists():
        return []

    delete_month: int = 3  # fichiers plus vieux que x mois à supprimer
    trigger_month: int = 4  # s'ils en existent plus vieux que y mois (évite d'en identifier tous les jours)

    old_files: list[Path] = []
    trigger: bool = False
    delete_treshold: datetime.date = datetime.now().date() + relativedelta(months=-delete_month)
    trigger_treshold: datetime.date = datetime.now().date() + relativedelta(months=-trigger_month)

    for file in folder.iterdir():
        if file in white_list:
            continue

        time_created = datetime.fromtimestamp(file.stat().st_ctime).date()  # date de création du fichier
        if time_created < trigger_treshold and not trigger:
            trigger = True

        if time_created < delete_treshold:
            old_files.append(file)

    return old_files if trigger else []


def old_files_delete(files: list[Path]) -> None:
    for file in files:
        file.unlink(missing_ok=True)


def most_recent_files(files: list[Path]) -> datetime:
    return max([datetime.fromtimestamp(file.stat().st_ctime) for file in files]) + relativedelta(days=1)


if __name__ == "__main__":
    extract_folder: Path = Path().cwd()  # / "TestFolder"

    files = old_files_list(extract_folder)  # liste des fichiers à supprimer
    files_nb = len(files)
    if files_nb:
        files_size = round(sum([size.stat().st_size for size in files]) / 1024**2, 2)
        files_date = most_recent_files(files)

        answer = input(
            "=" * 100
            + "\n"
            + f"Dans le dossier des extractions il existe {files_nb} fichiers "
            + f"datant d'avant le {files_date.strftime('%d/%m/%Y')}.\n"
            + f"Ils vont être supprimés pour libérer {files_size} Mo d'espace disque.\n"
            + "Vous pouvez choisir d'annuler la suppression mais ce message reviendra à chaque ouverture.\n"
            + "Si certains des fichiers doivent être conservés merci de les changer de répertoire.\n"
            + "=" * 100
            + "\n"
            + "Suppression des fichiers ? (Y pour oui, n pour non) : "
        )

        if answer == "Y":
            old_files_delete(files)
