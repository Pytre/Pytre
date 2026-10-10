"""
Builds tests/resources/Pytre_test.db - a KeePass database dedicated to integration tests,
committed to the repo so most integration tests can run without any personal setup. Uses
Servers/Users/Settings' own save() methods rather than poking at pykeepass entries directly, so
the fixture stays structurally identical to what the real app writes.

Run from the repo root: .env_314_linux/bin/python -m tests.resources.build_test_keepass
(Kee gets its password explicitly, so the developer's own Pytre.key is never touched)

To change a local docker test server's credentials (e.g. after rebuilding the mssql-test or
postgre-test container), edit TEST_SQL_SERVER/TEST_PG_SERVER below and re-run this script - no
test file needs touching.
"""

from pathlib import Path

import Pytre_Core.kee as kee_module
from Pytre_Core.kee import BLANK_PWD, Kee
from Pytre_Core.servers import Server, Servers
from Pytre_Core.settings import Settings
from Pytre_Core.users import AuthType, User, Users, hash_password

# Local-only docker container (mssql-test), no security criticality.
# Password pinned deliberately - update here if the container changes.
TEST_SQL_SERVER = {
    "id": "test",
    "description": "Local docker mssql-test container, used by integration tests",
    "host": "127.0.0.1",
    "port": "1433",
    "database": "master",
    "user": "sa",
    "password": "TonNouveauM0tDeP@sse2026!",
    "type": "mssql",
}

# Local-only docker container (postgre-test), no security criticality - see docker-compose.postgre.yml.
TEST_PG_SERVER = {
    "id": "test_pg",
    "description": "Local docker postgre-test container, used by integration tests",
    "host": "127.0.0.1",
    "port": "5432",
    "database": "postgres",
    "user": "postgres",
    "password": "TestPgP@ssw0rd2026!",
    "type": "postgre",
}

TEST_USERNAME = "test_user"  # synthetic, not a real person
TEST_USER_TITLE = "Test User"
TEST_USER_PASSWORD = "TestPassword123!"  # synthetic, only used to exercise /login in tests

RESOURCES_DIR = Path(__file__).parent
DB_PATH = RESOURCES_DIR / "Pytre_test.db"
QUERIES_FOLDER = "tests/resources/queries"  # relative to repo root, matches Settings.load()'s Path.cwd() resolution
LOGS_FOLDER = "tests/resources/logs"  # unused (LOGS_ARE_ON stays false) - just needs to not be Path.home()


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    kee_module.KEE_FILE = DB_PATH
    kee: Kee = Kee(password=BLANK_PWD)  # file doesn't exist yet -> Kee.create_db() runs, using BLANK_PWD regardless

    # remove the empty "Default" server entry that create_db() leaves behind
    assert kee.db is not None and kee.grp_servers is not None
    placeholder = kee.db.find_entries(title="Default", group=kee.grp_servers, first=True)
    if placeholder is not None:
        kee.db.delete_entry(placeholder)
        kee.save_db()

    servers = Servers(kee)
    server = Server(servers)
    server.id = TEST_SQL_SERVER["id"]
    server.description = TEST_SQL_SERVER["description"]
    server.host = TEST_SQL_SERVER["host"]
    server.port = TEST_SQL_SERVER["port"]
    server.database = TEST_SQL_SERVER["database"]
    server.user = TEST_SQL_SERVER["user"]
    server.password = TEST_SQL_SERVER["password"]
    server.type = TEST_SQL_SERVER["type"]
    server.trust_server_certificate = True  # mssql-test docker uses a self-signed certificate
    server.save()

    pg_server = Server(servers)
    pg_server.id = TEST_PG_SERVER["id"]
    pg_server.description = TEST_PG_SERVER["description"]
    pg_server.host = TEST_PG_SERVER["host"]
    pg_server.port = TEST_PG_SERVER["port"]
    pg_server.database = TEST_PG_SERVER["database"]
    pg_server.user = TEST_PG_SERVER["user"]
    pg_server.password = TEST_PG_SERVER["password"]
    pg_server.type = TEST_PG_SERVER["type"]
    pg_server.charset = "UTF8"
    pg_server.trust_server_certificate = True  # postgre-test docker uses a self-signed certificate
    pg_server.save()

    users = Users(kee)
    user = User(users, username=TEST_USERNAME, detect_user=False)
    user.title = TEST_USER_TITLE
    user.admin = True
    user.auth_type = AuthType.BASIC
    user.password_hash = hash_password(TEST_USER_PASSWORD)
    user.save()

    settings = Settings(kee)
    settings.queries_folder = Path(QUERIES_FOLDER)
    # LOGS_FOLDER defaults to Path.home() / "Logs" - must be overridden too, or the real local
    # username of whoever (re)generates this file gets baked into the committed .kdbx.
    settings.logs_folder = Path(LOGS_FOLDER)
    settings.save()

    # clean up the transient "./queries" folder create_db() creates by default (unused - we point
    # QUERIES_FOLDER at tests/resources/queries instead)
    leftover = Path("./queries")
    if leftover.exists() and not any(leftover.iterdir()):
        leftover.rmdir()

    print(f"Test KeePass database built at {DB_PATH}")


if __name__ == "__main__":
    main()
