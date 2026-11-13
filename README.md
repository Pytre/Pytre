# Pytre

This is the official repository of Pytre.  
In French it stands for "**Pyt**hon **Re**queteur" and it's a pun. A "pitre" in french is someone being a clown, a buffoon.

Pytre is a query tool to allow users to run prepackaged SQL queries on a Microsoft SQL Server or PostgreSQL database. Users don't need to know any SQL.

As it's developed and used for French users a lot of comments or explanations are in french. Internationalization is planned but no ETA.
It's a personal project and development is slow: I am working on it when I have time, and as it's already good enough for what it is intended for it's not high priority!

I am not looking for contributions but if you have any questions I would be happy to answer them.

## Contents

- [Architecture](#architecture)
- [Queries](#queries): writing them, where they are read from
- [Download](#download)
- [Enterprise deployment](#enterprise-deployment): MSI, zip, update policy
- [Server security](#server-security): reverse proxy, sign-in throttling, how queries are run, web administration
- [Server options](#server-options)
- [Setup from source](#setup-from-source)
- [License](#license)

## Architecture

- **Pytre_Core**: shared code (queries, settings, users, SQL drivers...).
- **Pytre_Client**: desktop application (Tkinter).
- **Pytre_Server**: HTTP server (FastAPI) executing queries on behalf of clients, with a web administration (users, servers, queries, settings) and optional SSO through OpenID Connect.

The client runs in one of two modes, detected at startup:

- **Hybrid**: the client sits next to a shared settings database (`Pytre.db`, typically on a network drive used by everyone). It connects directly to SQL servers, reads queries from a shared folder and can also use remote servers.
- **Pure client**: no settings database next to the client (typically installed on the user's computer). Queries are only run through one or more Pytre_Server instances ("remotes").

## Queries

Users run prepackaged queries: `.sql` files with a header describing them (name, description, allowed groups and servers...) and a `DECLARE` block for their parameters, which users fill in from the client. See [Writing queries](docs/queries.md) for their format.

- A hybrid client reads the queries from the shared queries folder, a server from its own one. The folder is set in the settings, from the client's settings window or the web administration: a `Queries` folder next to the application by default.
- In hybrid mode, the queries folder should contain `_version_min.json` (see `templates/`). It is used to make sure no one is using an old version when implementing new restrictions.

## Download

Windows builds are published on the [Releases](https://github.com/Pytre/Pytre/releases) page:
- client builds
    - `Pytre_Client_<version>.exe`: the client as a single portable executable
    - `Pytre_Client_<version>.zip`: the client as a folder, which starts faster
    - `Pytre_Client_<version>.msi`: the same folder as a Windows installer (stable releases only), with a policy file turning updates off
- server build
    - `Pytre_Server_<version>.exe`: the server as a single portable executable (console application with no automatic updates)

The executables and the zip need no installation: run the executable, or extract the zip in a folder you can write to and run `Pytre_Client.exe` inside it. The client executable and the zip update themselves, unless an [update policy](#update-policy) turns updates off. As they are not signed, Windows SmartScreen may show a warning on first launch (*More info > Run anyway*). The MSI is meant for managed deployments, see [Enterprise deployment](#enterprise-deployment).

The prebuilt client only runs as a pure client: hybrid mode needs `Pytre_Core/credentials_secrets.py` with your own key, so the client has to be built from source.

Before making the server reachable from other computers, read [Server security](#server-security): it doesn't handle HTTPS itself, and a reverse proxy in front of it must be set up as described there.

## Enterprise deployment

All three formats of the client suit a managed deployment. The server is only published as a single executable: to deploy it, see [Server security](#server-security).

- `Pytre_Client_<version>.msi` (recommended, stable releases only): installs the client folder, which never updates itself
- `Pytre_Client_<version>.zip`: the same folder, to extract yourself, also published for pre-releases
- `Pytre_Client_<version>.exe`: the client as a single executable

The zip and the executable update themselves: deploy them with a `Pytre.policy.json` file to control their updates, see [Update policy](#update-policy).

Releases are immutable and each one publishes `SHA256SUMS.txt`, so a file can be checked before being deployed, e.g. with `Get-FileHash <file> -Algorithm SHA256` in PowerShell.

### Installer (MSI)

- Silent install, with administrator rights: `msiexec /i Pytre_Client_<version>.msi /qn`. The client is installed for all users in `C:\Program Files\Pytre_Client`, with a Start menu shortcut.
- A newer version replaces the installed one. Installing an older version is refused.
- To go back to an older version: uninstall the client, then install the older MSI.
- To uninstall: "Installed apps" in Windows settings, or `msiexec /x Pytre_Client_<version>.msi /qn`.
- User data lives in `%LOCALAPPDATA%\Pytre` and is left untouched.
- The upgrade code `{66048ACB-7CC6-4AEE-9BC4-C0F307486C78}` stays the same across versions, for detection rules.
- It installs a `Pytre.policy.json` file turning updates off: new versions are deployed with a new MSI.

### Folder (zip)

- Extract the zip into a folder users can't write to, such as `C:\Program Files`: a `Pytre_Client` folder will be created there. If the zip was downloaded with a browser, run `Unblock-File` on it first.
- To update: close Pytre, delete the folder, then extract the new zip. Never extract over a previous version: files it no longer uses would stay behind.
- User data lives in `%LOCALAPPDATA%\Pytre` and is left untouched.
- To detect the installed version, read the file version of `Pytre_Client.exe`: `2.0.0.400` for 2.0.0, pre-releases being lower (`2.0.0.204` for 2.0.0-beta.4).
- Add a `Pytre.policy.json` file next to `Pytre_Client.exe` to turn updates off. Without it, the client checks for new versions and, as it can't replace itself in a folder users can't write to, offers to open the download page.

### Update policy

Put a `Pytre.policy.json` file next to the client executable:

```json
{ "update_policy": "disabled" }
```

| `update_policy` | Behaviour |
|---|---|
| `allowed` | Checks for updates at startup by default, and on demand from the `?` menu. Users can turn off the startup check and choose the update channel in the About window |
| `disabled` | Never checks for updates: the update menu is hidden, and the About window says updates are managed by the organization instead of showing the update settings |

- Values are case insensitive. Without the file, or without `update_policy`, the client behaves as `allowed`.
- A file that can't be read, isn't valid JSON or holds an unknown value disables updates, and a warning is logged.
- The policy only binds if users can't modify the file: install the executable and `Pytre.policy.json` in a folder users can't write to, such as `C:\Program Files\Pytre_Client`.
- Keep `Pytre.policy.json` next to the executable if the deployment recreates the folder or uses a new one for each version.

## Server security

### Network and reverse proxy

The server listens on `127.0.0.1` by default and doesn't handle HTTPS itself: put it behind a reverse proxy that terminates TLS (see `--forwarded-allow-ips`).

The proxy must add `X-Forwarded-For` and `X-Forwarded-Proto` headers to every request. Without them, a proxy running on the server itself makes every request look local: the actions reserved to the server itself, and the creation of the first admin account, would then be reachable from anywhere. Apache, Caddy and Traefik add them by default, nginx needs:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

The proxy must also be listed in `--forwarded-allow-ips`. Failed sign-ins are counted per client address (see [Sign-in throttling](#sign-in-throttling)), and without it every request seems to come from the proxy: 20 failed sign-ins would then block signing in for everyone.

The web administration (`/admin`) shares its port with the client API: the proxy can also restrict `/admin` to the networks admins work from.

### Sign-in throttling

Failed sign-ins, to the client API and to the web administration alike, are counted over the last 15 minutes. Past a threshold, sign-ins are refused (HTTP 429) without checking the password:

| Counted per | Threshold | Refused |
|---|---|---|
| account and client address | 5 | for this account, from this address |
| client address | 20 | for every account, from this address |
| account | 50 | for this account, from every address except the ones it was recently used from |

- A successful sign-in only resets the count of its account from its address: an attacker can't lock an account out from their own address, and a valid account doesn't restart the count of an address.
- An account targeted from many addresses stays usable from the addresses it signed in from, or refreshed its session from, in the last 30 days.
- At most 2 passwords are checked at once: beyond, a sign-in waits up to half a second for its turn, then is refused (HTTP 429).
- Counts are kept in memory only and lost when the server restarts.

### Running queries

A client never sends SQL to a server. For each execution, it only sends the server and query identifiers, picked from the lists the server gave it, the hash of its version of the query, and the parameter values. The server then:

- reads the query from its own queries folder, and checks if the user is allowed to run it on that server,
- refuses the execution if the client's version of the query differs from its own: the client has to refresh its list,
- checks the parameter names match the query and their values are valid (type, size, allowed values),
- replaces hidden parameters with the values it computes itself, unless the user is an admin,
- caps the number of executions running at once for each user, and logs each execution with its parameters.

Values are passed to the database as query parameters, except for `@!` parameters, which are written into the SQL text and only accept a value from the query's list of allowed values.

Database credentials stay on the server: a client only ever gets the identifier and description of each SQL server.

### Web administration

The web administration is served at `/admin`, by the same process and on the same port as the client API. Opening the server's address in a browser redirects to it.

- Only admin users can sign in, with their password or through SSO. The first admin account can only be created from the server itself (localhost).
- Repeated failed sign-ins are throttled, see [Sign-in throttling](#sign-in-throttling).
- The CSV exports only include the passwords of the servers and the password hashes of the users when made from the server itself. Changing the host, port, instance or type of a server, or whether its certificate is checked, requires entering its password again, in its form as in a CSV import.
- A session ends after 1 hour of inactivity and after 8 hours in any case, or as soon as the user loses admin rights or changes their password.
- Queries are files in the server's queries folder: the web administration lists them, reloads them from disk, and can exempt one from the version check for a limited time.
- An admin can reveal, replace or delete the master password of the settings database stored in the keyring, and re-encrypt the database with a new one, only from the server itself (e.g. `http://127.0.0.1:8000/admin`, or through an SSH tunnel): requests through a reverse proxy are refused. Each of these actions is logged as a warning.

## Server options

The same options apply to `Pytre_Server.exe` and `python -m Pytre_Server`, `--help` lists them.

| Option | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Address to listen on, `0.0.0.0` for every interface. Keep the default when the reverse proxy runs on the server itself |
| `--port` | `8000` | Port to listen on |
| `--forwarded-allow-ips` | `127.0.0.1`, or `$FORWARDED_ALLOW_IPS` | Comma-separated addresses of the reverse proxies whose `X-Forwarded-*` headers are trusted. Never `*` unless the server can only be reached through the proxy |
| `--chunk-size` | `2000` | Rows sent per chunk of a query result |
| `--db-pool-size` | `30` | Maximum number of queries running at once on each SQL server |
| `--max-concurrency-per-user` | `4` | Maximum number of requests running at once for each user |
| `--heartbeat-secs` | `30` | Seconds between keep-alive pings while a query runs, `0` to disable them |
| `--log-file` | none | File the logs are written to, also used to refill the web administration logs at startup |
| `--log-buffer-mb` | `5` | Size of the logs kept in memory for the web administration, in MB |

Maintenance commands, run one at a time without starting the server:

| Command | Description |
|---|---|
| `--hash-password` | Prompts for a password and prints its hash |
| `--show-master-key` | Prints the master password stored in the system keyring |
| `--set-master-key` | Prompts for a new master password and stores it in the system keyring |
| `--delete-master-key` | Deletes the master password from the system keyring |

## Setup from source

Requires Python 3.14 or later (tested with 3.14).

### Client

```
pip install -r requirements.txt
python -m Pytre_Client
```

- Without `Pytre.db` next to the application, the client starts as a pure client: add remotes from *Préférences > Remotes...*.
- To set up a new shared (hybrid) installation, copy `templates/credentials_secrets.py` to `Pytre_Core/` and change its key, then launch the client once with `--init-hybrid`: `Pytre.db` and `Pytre.key` (the database password, encrypted with that key) are created and the current user is given admin rights.

Then put the queries and `_version_min.json` in the queries folder, see [Queries](#queries).

User preferences, remotes, local logs and cache are stored in the local application data folder:
- on Windows: `%LOCALAPPDATA%\Pytre`,
- on Linux: `~/.local/share/Pytre` (or `$XDG_DATA_HOME/Pytre`),
- on macOS: `~/Library/Application Support/Pytre`.

### Server

```
pip install -r requirements-server.txt
python -m Pytre_Server --help
```

On first start the master password of the settings database is asked in the terminal and stored in the system keyring (see [Server options](#server-options) to manage it afterwards). The web administration is available at `/admin`; the first admin account can only be created from the server itself (localhost). Behind a reverse proxy, see [Server security](#server-security).

### Building executables

```
pip install -r requirements-build.lock
pyinstaller Pytre_Client.spec                                      # single executable
pyinstaller --workpath build/onedir Pytre_Client.spec -- --onedir  # folder, used for the zip and the MSI
pyinstaller Pytre_Server.spec
```

Executables are written to `dist/`, the client folder to `dist/Pytre_Client/`: `Pytre_Client.exe` and its `_internal_<version>` folder, a new version being extracted next to the running one when the client updates itself.  
The MSI is built from that folder with WiX 7 and `Pytre_Client.wxs`, see the "Build client installer" step of `.github/workflows/build.yml`.  
`requirements-build.lock` pins the exact versions used for releases, regenerate it with `requirements-update_lock.sh` after changing `requirements*.txt`.

## License

Pytre is licensed under GNU Affero General Public License 3. You can find the license text in the LICENSE file.

## Main libraries used

- Client: [pymssql](https://github.com/pymssql/pymssql), [psycopg2](https://github.com/psycopg/psycopg2), [httpx2](https://github.com/pydantic/httpx2)
- Server: [FastAPI](https://github.com/fastapi/fastapi), [uvicorn](https://github.com/encode/uvicorn), [pymssql](https://github.com/pymssql/pymssql), [fastmssql](https://github.com/Rivendael/FastMssql), [asyncpg](https://github.com/MagicStack/asyncpg), [Authlib](https://github.com/authlib/authlib), [PyJWT](https://github.com/jpadilla/pyjwt), [argon2-cffi](https://github.com/hynek/argon2-cffi), [Jinja2](https://github.com/pallets/jinja)
- Both: [pykeepass](https://github.com/libkeepass/pykeepass), [cryptography](https://github.com/pyca/cryptography), [keyring](https://github.com/jaraco/keyring)

## Credits

- App icon from [FlatIcon.com](https://www.flaticon.com/free-icon/buffoon_688319)
