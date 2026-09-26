"""
Tests for the form models of the admin routes - ServerForm.apply_to() (Pytre_Server/routes/admin_servers.py)
and UserForm.apply_to() (Pytre_Server/routes/admin_users.py).

They copy submitted values onto a Server/User. Field names differ on purpose (login -> user,
groups -> grp_authorized), which makes a wrong attribute easy to write and silent at runtime,
hence testing the mapping without going through HTTP or KeePass.
"""

from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from Pytre_Core.servers import Server, ServerType
from Pytre_Core.users import AuthType, User, verify_password
from Pytre_Server.routes.admin_servers import ServerForm
from Pytre_Server.routes.admin_users import UserForm


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ================================================================================
# ServerForm.apply_to
# ================================================================================


def _server_form(**overrides: Any) -> ServerForm:
    data: dict[str, Any] = {"id": "srv1", "type": ServerType.mssql.value, "login_timeout": 60, "timeout": 300}
    return ServerForm.model_validate({**data, **overrides})


def test_server_form_apply_to_copies_every_field():
    server = Server(Mock())
    server.grp_authorized = ["previous"]
    form = ServerForm(
        id="  srv1  ",
        description=" A server ",
        login=" sa ",
        password="secret",
        type=ServerType.postgre.value,
        charset=" utf8 ",
        database=" db ",
        host=" host ",
        port=" 5432 ",
        server=" inst ",
        login_timeout=10,
        timeout=20,
        trust_server_certificate=True,
        groups=["sales", "it"],
    )

    form.apply_to(server)

    assert server.id == "srv1"
    assert server.description == "A server"
    assert server.user == "sa"  # login -> user
    assert server.password == "secret"
    assert server.type == ServerType.postgre.name  # the enum's name is stored, not its display value
    assert server.charset == "utf8"
    assert server.database == "db"
    assert server.host == "host"
    assert server.port == "5432"
    assert server.server == "inst"
    assert server.login_timeout == 10
    assert server.timeout == 20
    assert server.trust_server_certificate is True
    assert server.grp_authorized == ["sales", "it"]  # groups -> grp_authorized, replacing the previous ones


def test_server_form_apply_to_keeps_the_existing_password_when_blank():
    server = Server(Mock())
    server.password = "existing"

    _server_form(password="").apply_to(server)
    assert server.password == "existing"

    _server_form(password="changed").apply_to(server)
    assert server.password == "changed"


def test_server_form_apply_to_falls_back_on_mssql_for_an_unknown_type():
    server = Server(Mock())
    server.type = ServerType.postgre.name

    _server_form(type="Oracle").apply_to(server)

    assert server.type == ServerType.mssql.name


def test_server_form_unchecked_trust_checkbox_means_untrusted():
    server = Server(Mock())
    server.trust_server_certificate = True

    _server_form().apply_to(server)  # an unchecked checkbox sends no field at all

    assert server.trust_server_certificate is False


def test_server_form_requires_the_timeouts():
    """Left without a default on purpose: Server's own defaults (60/300) shouldn't be silently
    replaced by a second, different default living in the form."""
    with pytest.raises(ValidationError):
        ServerForm.model_validate({"id": "srv1", "type": ServerType.mssql.value})


# ================================================================================
# UserForm.apply_to
# ================================================================================


def _user_form(**overrides: Any) -> UserForm:
    data: dict[str, Any] = {"username": "jdoe", "auth_type": AuthType.NONE.value}
    return UserForm.model_validate({**data, **overrides})


@pytest.mark.anyio
async def test_user_form_apply_to_copies_every_field():
    user = User(Mock(), detect_user=False)
    user.attribs_cust = {"kept": "stays", "dept": "old"}
    form = UserForm(
        username="  jdoe  ",
        title=" Doe ",
        admin=True,
        msg_login=" hello ",
        auth_type="basic",
        groups=["sales", "it"],
    )

    await form.apply_to(user, {"dept": "IT"})

    assert user.username == "jdoe"
    assert user.title == "Doe"
    assert user.admin is True
    assert user.msg_login == "hello"
    assert user.auth_type == AuthType.BASIC
    assert user.groups == ["sales", "it"]  # replaces the default ["all"], not added to it
    assert user.attribs_cust == {"kept": "stays", "dept": "IT"}  # updated, the others left alone


@pytest.mark.anyio
async def test_user_form_apply_to_hashes_the_password():
    user = User(Mock(), detect_user=False)

    await _user_form(auth_type="basic", password="s3cret!").apply_to(user, {})

    assert user.password_hash != "s3cret!"
    assert verify_password("s3cret!", user.password_hash)


@pytest.mark.anyio
async def test_user_form_apply_to_keeps_the_existing_hash_when_password_is_blank():
    user = User(Mock(), detect_user=False)
    user.password_hash = "existing-hash"

    await _user_form(password="").apply_to(user, {})

    assert user.password_hash == "existing-hash"


@pytest.mark.anyio
async def test_user_form_apply_to_falls_back_on_none_for_an_unknown_auth_type():
    user = User(Mock(), detect_user=False)
    user.auth_type = AuthType.BASIC

    await _user_form(auth_type="kerberos").apply_to(user, {})

    assert user.auth_type == AuthType.NONE


@pytest.mark.anyio
async def test_user_form_unchecked_admin_checkbox_means_not_admin():
    """Regression: an unchecked checkbox sends no field, so admin must fall back to False (not be
    tested against None, which made every saved user an admin)."""
    user = User(Mock(), detect_user=False)
    user.admin = True

    await _user_form().apply_to(user, {})

    assert user.admin is False
