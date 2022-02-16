import os
import typing

import sql_query

SETTINGS = sql_query.SETTINGS
PRINT_DATE_FORMAT = "%d/%m/%Y at %H:%M:%S"  # pour le format de la date à écrire dans la console


def main():
    my_user: sql_query.settings.User = SETTINGS.user

    if not my_user.exist_in_settings and my_user.domain == SETTINGS.domain_user_auto_add:
        sql_query.create_user_in_settings()

    if my_user.is_authorized:
        print(my_user.msg_login)
        queries = sql_query.get_queries(SETTINGS.queries_folder)

        continue_loop = True
        while continue_loop:
            continue_loop = menu(queries)
            clear_console()
    else:
        print(
            str("=") * 100
            + f"\nDésolé, vous n'êtes pas dans liste des utilisateurs autorisées !\n"
            + str("=") * 100
            + "\n"
        )


def menu(queries: typing.List[sql_query.Query]):
    query = choose_file(queries)  # selection de la requête à utiliser
    clear_console()

    if query == "":
        return False  # stopper si aucun fichier sélectionné

    if input_param(query):
        query.execute_cmd()

    input("Appuyer sur n'importe quelle touche pour continuer")

    return True


def choose_file(queries: typing.List[sql_query.Query]):
    print(str("=") * 100 + f"\nListe des requêtes disponibles\n" + str("=") * 100 + "\n")

    for i, query in enumerate(queries, start=1):
        print(f"{i} : {query.name} - {query.description}")
    print("\n0 : Quitter")

    while True:
        choice = input("\n" + str("=") * 100 + "\nMerci d'indiquer le numéro de la requête à executer : ")
        choice = int(choice)
        if choice >= 0 and choice <= len(queries):
            if choice > 0:
                query = queries[choice - 1]
            else:
                query = ""
            break

    return query


def input_param(query: sql_query.Query):
    params = query.params_obj
    my_user: sql_query.settings.User = SETTINGS.user

    txt_header = f"{query.name} - {query.description}" if not query.description == "" else query.name
    print(str("=") * 100 + f"\nSaisie des paramètres pour {txt_header} :\n" + str("=") * 100)

    params_number_not_hidden = 0
    if not params is None:
        keys = [p for p in params if not params[p].is_hidden or my_user.superuser]
        params_number_not_hidden = len(keys)

    if params_number_not_hidden == 0:
        print("pas de paramètre pour cette requête !")

    for key in keys:  # si pas de paramètres jamais executé
        if params[key].is_hidden and not my_user.superuser:
            continue

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
