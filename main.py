import os
from pathlib import Path

import settings, sql_user, sql_query

CWD = Path.cwd()  # dossier du script ou de l'executable
APP_PATH = settings.APP_PATH  # dossier ou les fichiers de l'executable sont extraits
PRINT_DATE_FORMAT = "%d/%m/%Y at %H:%M:%S"  # pour le format de la date à écrire dans la console


def main():
    my_user = sql_user.User()
    if my_user.is_authorized:
        print(my_user.msg_login)

        continue_loop = True
        while continue_loop:
            continue_loop = menu()
            clear_console()
    else:
        print(
            str("=") * 100
            + f"\nDésolé, vous n'êtes pas dans liste des utilisateurs autorisées !\n"
            + str("=") * 100
            + "\n"
        )


def menu():
    sql_file = choose_file(APP_PATH / settings.QUERY_FOLDER)  # selection de la requête à utiliser
    clear_console()

    if sql_file == "":
        return False  # stopper si aucun fichier sélectionné

    my_query = sql_query.Query(sql_file)

    if input_param(my_query):
        my_query.execute_cmd()

    input("Appuyer sur n'importe quelle touche pour continuer")

    return True


def choose_file(folder):
    files = [files for files in Path(folder).iterdir() if files.is_file() and files.suffix == ".sql"]

    print(str("=") * 100 + f"\nListe des requêtes disponibles\n" + str("=") * 100 + "\n")

    for i, file in enumerate(files, start=1):
        print(f"{i} : {file.stem}")
    print("\n0 : Quitter")

    while True:
        choice = input("\n" + str("=") * 100 + "\nMerci d'indiquer le numéro de la requête à executer : ")
        choice = int(choice)
        if choice >= 0 and choice <= len(files):
            if choice > 0:
                file = files[choice - 1]
            else:
                file = ""
            break

    return file


def input_param(query: sql_query.Query):
    params = query.params

    txt_header = f"{query.name} - {query.description}" if not query.description == "" else query.name
    print(str("=") * 100 + f"\nSaisie des paramètres pour {txt_header} :\n" + str("=") * 100)

    if params == {}:
        print("pas de paramètre pour cette requête !")

    for key in params:  # si pas de paramètres jamais executé
        input_txt = params[key].description
        if not params[key].display_value == "":
            input_txt += f" (param.par défaut est {params[key].display_value})"

        while True:
            params[key].display_value = input(input_txt + " : ")
            try:
                query.update_values(key)
                break
            except ValueError as e:
                print(e)

    if input(str("=") * 100 + "\nMerci de confirmez (y) pour lancer l'extraction : ").lower() == "y":
        return True
    else:
        return False


def clear_console():
    if os.name == "nt":  # for windows
        _ = os.system("cls")
    else:  # for mac and linux(here, os.name is 'posix')
        _ = os.system("clear")


if __name__ == "__main__":
    clear_console()
    main()
