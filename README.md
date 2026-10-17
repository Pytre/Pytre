# Pytre

This is the official repository of Pytre.  
In French it stands for "**Pyt**hon **Re**queteur" and it's a pun. A "pitre" in french is someone being a clown, a buffoon.

Pytre is a query tool to allow users to run prepackaged SQL queries on a Microsoft SQL Server or PostgreSQL database. Users don't need to know any SQL.

As it's developed and used for French users a lot of comments or explanations are in french. Internationalization is planned but no ETA.
It's a personal project and development is slow: I am working on it when I have time, and as it's already good enough for what it is intended for it's not high priority!

I am not looking for contributions but if you have any questions I would be happy to answer them.

## Architecture

- **Pytre_Core**: shared code (queries, settings, users, SQL drivers...).
- **Pytre_Client**: desktop application (Tkinter).
- **Pytre_Server**: HTTP server (FastAPI) executing queries on behalf of clients, with a web administration (users, servers, queries, settings) and optional SSO through OpenID Connect.

The client runs in one of two modes, detected at startup:

- **Hybrid**: the client sits next to a shared settings database (`Pytre.db`, typically on a network drive used by everyone). It connects directly to SQL servers, reads queries from a shared folder and can also use remote servers.
- **Pure client**: no settings database next to the client (typically installed on the user's computer). Queries are only run through one or more Pytre_Server instances ("remotes").

## Download

Windows executables of the client (`Pytre_Client_<version>.exe`) and the server (`Pytre_Server_<version>.exe`) are published on the [Releases](https://github.com/Pytre/Pytre/releases) page. They are portable: no installation is needed, just download and run them. As they are not signed, Windows SmartScreen may show a warning on first launch (*More info > Run anyway*).

The prebuilt client only runs as a pure client: hybrid mode needs `Pytre_Core/credentials_secrets.py` with your own key, so the client has to be built from source.

## Setup from source

Requires Python 3.14 or later (tested with 3.14).

### Client

```
pip install -r requirements.txt
python -m Pytre_Client
```

- Without `Pytre.db` next to the application, the client starts as a pure client: add remotes from *Préférences > Remotes...*.
- To set up a new shared (hybrid) installation, copy `templates/credentials_secrets.py` to `Pytre_Core/` and change its key, then launch the client once with `--init-hybrid`: `Pytre.db` and `Pytre.key` (the database password, encrypted with that key) are created and the current user is given admin rights.

In hybrid mode, the queries folder should contain `_version_min.json` (see `templates/`). It is used to make sure no one is using an old version when implementing new restrictions.

User preferences, remotes and cache are stored in the user's home folder (`~/Pytre`).

### Server

```
pip install -r requirements-server.txt
python -m Pytre_Server --help
```

On first start the master password of the settings database is asked in the terminal and stored in the system keyring (`--show-master-key`, `--set-master-key` and `--delete-master-key` manage it afterwards). The web administration is available at `/admin`; the first admin account can only be created from the server itself (localhost).

### Queries

Queries are `.sql` files with a header describing them (code, description, allowed groups and servers...) and a `DECLARE` block for their parameters. See `docs/Query Template FR.txt`.

### Building executables

```
pip install -r requirements-build.lock
pyinstaller Pytre_Client.spec
pyinstaller Pytre_Server.spec
```

Executables are written to `dist/`. `requirements-build.lock` pins the exact versions used for releases, regenerate it with `requirements-update_lock.sh` after changing `requirements*.txt`.

## License

Pytre is licensed under GNU Affero General Public License 3. You can find the license text in the LICENSE file.

## Main libraries used

- Client: [pymssql](https://github.com/pymssql/pymssql), [psycopg2](https://github.com/psycopg/psycopg2), [httpx2](https://github.com/pydantic/httpx2)
- Server: [FastAPI](https://github.com/fastapi/fastapi), [uvicorn](https://github.com/encode/uvicorn), [fastmssql](https://github.com/Rivendael/FastMssql), [asyncpg](https://github.com/MagicStack/asyncpg), [Authlib](https://github.com/authlib/authlib), [PyJWT](https://github.com/jpadilla/pyjwt), [argon2-cffi](https://github.com/hynek/argon2-cffi), [Jinja2](https://github.com/pallets/jinja)
- Both: [pykeepass](https://github.com/libkeepass/pykeepass), [cryptography](https://github.com/pyca/cryptography), [keyring](https://github.com/jaraco/keyring)

## Credits

- App icon from [FlatIcon.com](https://www.flaticon.com/free-icon/buffoon_688319)
