from pathlib import Path

import config, utils, sql_user, sql_query

CWD = Path.cwd()  # dossier du script ou de l'executable
APP_PATH = Path(utils.get_app_path())  # dossier ou les fichiers de l'executable sont extraits
PRINT_DATE_FORMAT = "%d/%m/%Y at %H:%M:%S"  # pour le format de la date à écrire dans la console


def main():
    print(str("=") * 100 + f"\nListe des requêtes disponibles\n" + str("=") * 100 + "\n")

    sql_file = choose_file(APP_PATH / config.QUERY_FOLDER)  # selection de la requête à utiliser
    if sql_file == "":
        return False  # stopper si aucun fichier sélectionné
    else:
        utils.clear_console()

    my_query = sql_query.Query(sql_file)
    sql_cmd_from_file(my_query)  # récupération de la commande SQL avec les inputs de l'utilisateur
    my_query.execute_cmd()
    input("Appuyer sur n'importe quelle touche pour continuer")

    return True


def choose_file(folder):
    files = [files for files in Path(folder).iterdir() if files.is_file() and files.suffix == ".sql"]

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


def sql_cmd_from_file(query: sql_query.Query):
    parameters = query.params
    if not parameters == {}:
        txt_header = f"{query.name} - {query.description}" if not query.description == "" else query.name
        print(str("=") * 100 + f"\nSaisie des paramètres pour {txt_header} :\n" + str("=") * 100)
        if not _sql_cmd_get_user_input(query):
            return False


def _sql_cmd_get_user_input(query: sql_query.Query):
    params = query.params
    for key in params:
        while True:
            if not params[key].display_value == "":  # texte saisie si paramètre par défaut existe ou pas
                input_txt = f"{params[key].description} (param.par défaut est {params[key].display_value}) : "
            else:
                input_txt = f"{params[key].description} : "

            params[key].display_value = input(input_txt)
            try:
                if query.update_values(key):
                    break
            except ValueError as e:
                print(e)

    if input(str("=") * 100 + "\nMerci de confirmez vos paramètres (y) pour lancer l'extraction : ").lower() == "y":
        return True
    else:
        return False


if __name__ == "__main__":
    my_user = sql_user.User()

    if my_user.is_authorized:
        msg_login = my_user.get_parameter("msg_login")

        continue_loop = True
        while continue_loop:
            utils.clear_console()
            if not msg_login is None and not msg_login == "":
                print(msg_login)
            else:
                msg_login = ""

            continue_loop = main()
    else:
        print(
            str("=") * 100
            + f"\nDésolé, vous n'êtes pas dans liste des utilisateurs autorisées !\n"
            + str("=") * 100
            + "\n"
        )
