"""
Tests for Pytre_Core/oidc_providers.py - OidcProviders (the collection) and OidcProvider (one provider), stored as entries
of the dedicated OIDC KeePass group - and for User.oidc_link, the (at most one) link between a
Pytre user and an identity provider account.

They run against a real KeePass database (a per-test copy of tests/resources/Pytre_test.db) rather
than mocks: what matters here is how fields land in an entry (title/username/password/custom
properties) and come back, which a mock would only echo.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

import Pytre_Core.kee as kee_module
from Pytre_Core.kee import Kee
from Pytre_Core.oidc_providers import OidcProvider, OidcProviders
from Pytre_Core.users import AuthType, OidcLink, User, Users

TEST_DB_PATH = Path(__file__).parent.parent.parent / "resources" / "Pytre_test.db"
TEST_DB_PASSWORD = "password"

ISSUER = "https://idp.example.com/realms/pytre"

pytestmark = pytest.mark.unit


@pytest.fixture
def kee(monkeypatch, tmp_path) -> Kee:
    db_copy = tmp_path / "Pytre_test.db"
    shutil.copy(TEST_DB_PATH, db_copy)
    monkeypatch.setattr(kee_module, "KEE_FILE", db_copy)
    return Kee(password=TEST_DB_PASSWORD)


@pytest.fixture
def oidcs(kee) -> OidcProviders:
    return OidcProviders(kee)


def _new_oidc(oidcs: OidcProviders, issuer: str = ISSUER, **overrides) -> OidcProvider:
    oidc = OidcProvider(oidcs)
    oidc.issuer = issuer
    oidc.client_id = "pytre-client"
    oidc.client_secret = "s3cret"
    oidc.enabled = True
    for name, value in overrides.items():
        setattr(oidc, name, value)
    return oidc


def _reopened() -> OidcProviders:
    """A fresh Kee/Oidcs on the same file (the `kee` fixture points KEE_FILE at its copy) - proves what
    was saved is what a new process would read."""
    return OidcProviders(Kee(password=TEST_DB_PASSWORD))


# ================================================================================
# Oidcs / Oidc - defaults and round trip
# ================================================================================


def test_a_fresh_database_has_no_oidc(oidcs):
    assert oidcs.get_all_providers() == {}
    assert oidcs.get_usable_providers() == {}


def test_a_new_oidc_has_sensible_defaults(oidcs):
    oidc = OidcProvider(oidcs)

    assert oidc.enabled is False
    assert oidc.label == ""
    assert oidc.scopes == "openid"
    assert oidc.username_claim == "preferred_username"
    assert oidc.public_base_url == ""
    assert oidc.uuid == ""


def test_save_then_reload_from_a_new_kee_keeps_every_field(kee, oidcs):
    oidc = _new_oidc(
        oidcs,
        label="Company SSO",
        scopes="openid email",
        username_claim="email",
        public_base_url="https://pytre.example.com",
    )

    assert oidc.save() is True
    assert oidc.uuid != ""

    loaded = _reopened().get_all_providers()[ISSUER]
    assert loaded.uuid == oidc.uuid
    assert loaded.issuer == ISSUER
    assert loaded.client_id == "pytre-client"
    assert loaded.client_secret == "s3cret"
    assert loaded.enabled is True
    assert loaded.label == "Company SSO"
    assert loaded.scopes == "openid email"
    assert loaded.username_claim == "email"
    assert loaded.public_base_url == "https://pytre.example.com"


# ================================================================================
# label - falls back to the issuer's host when not explicitly set
# ================================================================================


def test_label_falls_back_to_the_issuer_host_when_blank(oidcs):
    oidc = _new_oidc(oidcs, issuer="https://idp.example.com/realms/pytre")

    assert oidc.label == "idp.example.com"


def test_label_falls_back_to_the_raw_issuer_when_it_has_no_host(oidcs):
    oidc = _new_oidc(oidcs, issuer="not-a-url")

    assert oidc.label == "not-a-url"


def test_an_explicit_label_overrides_the_issuer_host_fallback(oidcs):
    oidc = _new_oidc(oidcs, issuer="https://idp.example.com", label="Company SSO")

    assert oidc.label == "Company SSO"


def test_label_fallback_is_not_persisted_only_an_explicit_label_is(kee, oidcs):
    _new_oidc(oidcs).save()  # no explicit label - relies on the issuer-host fallback

    loaded = _reopened().get_all_providers()[ISSUER]
    assert loaded.label == "idp.example.com"  # still derived, not "" nor a stale computed value


def test_the_secret_lands_in_the_protected_password_field_and_the_issuer_in_the_title(kee, oidcs):
    _new_oidc(oidcs).save()

    entry = kee.grp_oidc.entries[0]
    assert entry.title == ISSUER
    assert entry.username == "pytre-client"
    assert entry.password == "s3cret"


def test_saving_again_updates_the_same_entry(kee, oidcs):
    oidc = _new_oidc(oidcs)
    oidc.save()
    oidc.client_secret = "rotated"
    oidc.enabled = False
    oidc.save()

    assert len(kee.grp_oidc.entries) == 1
    loaded = _reopened().get_all_providers()[ISSUER]
    assert loaded.client_secret == "rotated"
    assert loaded.enabled is False


def test_reload_picks_up_a_change_made_elsewhere(kee, oidcs):
    oidc = _new_oidc(oidcs)
    oidc.save()

    other = _reopened().get_all_providers()[ISSUER]
    other.scopes = "openid profile"
    other.save()

    assert oidc.scopes == "openid"
    oidc.reload()
    assert oidc.scopes == "openid profile"


def test_delete_removes_the_entry(kee, oidcs):
    oidc = _new_oidc(oidcs)
    oidc.save()

    assert oidc.delete() is True

    assert _reopened().get_all_providers() == {}


def test_delete_of_an_unknown_oidc_raises(oidcs):
    ghost = _new_oidc(oidcs)
    ghost.uuid = "00000000-0000-0000-0000-000000000000"

    with pytest.raises(LookupError):
        ghost.delete()


# ================================================================================
# find_provider_by_uuid - used by the admin webui's edit/delete routes
# ================================================================================


def test_find_provider_by_uuid_returns_a_usable_provider(kee, oidcs):
    oidc = _new_oidc(oidcs, label="Company SSO")
    oidc.save()

    found = oidcs.find_provider_by_uuid(oidc.uuid)

    assert found is not None
    assert found.uuid == oidc.uuid
    assert found.issuer == ISSUER
    assert found.label == "Company SSO"


def test_find_provider_by_uuid_returns_none_for_an_unknown_uuid(oidcs):
    assert oidcs.find_provider_by_uuid("00000000-0000-0000-0000-000000000000") is None


def test_find_provider_by_uuid_needs_an_explicit_reload_to_see_a_change_made_elsewhere(kee, oidcs):
    """find_provider_by_uuid() doesn't reload on its own (same as Servers.find_server_by_uuid()) -
    a change saved through a separate OidcProviders/Kee stays invisible until reload=True."""
    oidc = _new_oidc(oidcs)
    oidc.save()

    other = _reopened().find_provider_by_uuid(oidc.uuid)
    assert other is not None
    other.scopes = "openid profile"
    other.save()

    stale = oidcs.find_provider_by_uuid(oidc.uuid)
    assert stale is not None
    assert stale.scopes == "openid"

    oidcs.open_db(reload=True)
    fresh = oidcs.find_provider_by_uuid(oidc.uuid)
    assert fresh is not None
    assert fresh.scopes == "openid profile"


# ================================================================================
# raw_label - the explicitly-set label, without the issuer-host fallback
# ================================================================================


def test_raw_label_is_blank_when_no_explicit_label_was_set(oidcs):
    oidc = _new_oidc(oidcs, issuer="https://idp.example.com")

    assert oidc.raw_label == ""
    assert oidc.label == "idp.example.com"  # the public property still falls back


def test_raw_label_returns_the_explicit_label_when_set(oidcs):
    oidc = _new_oidc(oidcs, label="Company SSO")

    assert oidc.raw_label == "Company SSO"


def test_raw_label_round_trips_through_the_database_as_blank(kee, oidcs):
    _new_oidc(oidcs).save()  # no explicit label

    loaded = _reopened().get_all_providers()[ISSUER]
    assert loaded.raw_label == ""


# ================================================================================
# Oidc.save - validation
# ================================================================================


@pytest.mark.parametrize("issuer", ["", "   "])
def test_save_refuses_a_blank_issuer(oidcs, issuer):
    with pytest.raises(ValueError, match="issuer"):
        _new_oidc(oidcs, issuer=issuer).save()


@pytest.mark.parametrize("client_id", ["", "   "])
def test_save_refuses_a_blank_client_id(oidcs, client_id):
    with pytest.raises(ValueError, match="client id"):
        _new_oidc(oidcs, client_id=client_id).save()


def test_save_accepts_a_draft_without_client_secret_but_it_is_not_usable(kee, oidcs):
    """A provider can be saved before its secret is known; it just can't be used yet."""
    _new_oidc(oidcs, client_secret="").save()

    loaded = _reopened().get_all_providers()[ISSUER]
    assert loaded.client_secret == ""
    assert loaded.is_usable is False


def test_save_refuses_a_duplicate_issuer(oidcs):
    _new_oidc(oidcs).save()

    with pytest.raises(ValueError, match="déjà défini"):
        _new_oidc(oidcs).save()


def test_save_without_a_keepass_group_raises_a_clear_error(oidcs):
    oidcs.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group"):
        _new_oidc(oidcs).save()


def test_save_with_save_db_false_leaves_the_file_untouched(kee, oidcs):
    oidc = _new_oidc(oidcs)

    oidc.save(save_db=False)

    assert _reopened().get_all_providers() == {}  # not persisted until the caller saves the database
    kee.save_db()
    assert ISSUER in _reopened().get_all_providers()


# ================================================================================
# is_usable / get_usable_oidcs
# ================================================================================


def test_a_complete_enabled_oidc_is_usable(oidcs):
    assert _new_oidc(oidcs).is_usable is True


@pytest.mark.parametrize(
    "field, value",
    [("enabled", False), ("issuer", ""), ("client_id", ""), ("client_secret", "")],
)
def test_an_oidc_missing_one_requirement_is_not_usable(oidcs, field, value):
    assert _new_oidc(oidcs, **{field: value}).is_usable is False


def test_get_usable_oidcs_excludes_disabled_and_incomplete_providers(oidcs):
    _new_oidc(oidcs, issuer="https://ok.example.com").save()
    _new_oidc(oidcs, issuer="https://disabled.example.com", enabled=False).save()
    _new_oidc(oidcs, issuer="https://no-secret.example.com", client_secret="").save()

    assert set(oidcs.get_all_providers()) == {
        "https://ok.example.com",
        "https://disabled.example.com",
        "https://no-secret.example.com",
    }
    assert set(oidcs.get_usable_providers()) == {"https://ok.example.com"}


# ================================================================================
# Entries with unfilled fields
# ================================================================================


def test_an_entry_without_title_username_or_password_loads_as_empty_strings(kee, oidcs):
    """pykeepass returns None for a field that was never filled. The provider's attributes must
    still be str: str(None) would give the text "None" (a non-blank issuer that passes every check)."""
    kee.db.add_entry(kee.grp_oidc, "", "", "")
    entry = kee.grp_oidc.entries[0]
    for key in ("Title", "UserName", "Password"):  # what a hand-made entry with those fields left out looks like
        field = entry._element.xpath(f"String[Key='{key}']")[0]
        field.getparent().remove(field)
    assert entry.title is None and entry.username is None and entry.password is None

    loaded = OidcProvider(oidcs, entry=entry)

    assert (loaded.issuer, loaded.client_id, loaded.client_secret) == ("", "", "")
    assert loaded.is_usable is False


# ================================================================================
# User.oidc_link
# ================================================================================


def test_a_new_user_has_no_oidc_link(kee):
    assert User(Users(kee), detect_user=False).oidc_link is None


def test_oidc_link_and_auth_type_round_trip_through_the_database(kee):
    users = Users(kee)
    user = User(users, detect_user=False)
    user.username = "sso_user"
    user.auth_type = AuthType.OIDC
    user.oidc_link = OidcLink(
        issuer=ISSUER, sub="opaque-sub-123", refresh_token="rt-abc", last_verified=datetime.now(timezone.utc)
    )
    user.save()

    loaded = User(Users(Kee(password=TEST_DB_PASSWORD)), username="sso_user")

    assert loaded.exists is True
    assert loaded.auth_type is AuthType.OIDC
    linked = loaded.oidc_link
    assert linked is not None
    assert linked.issuer == ISSUER
    assert linked.sub == "opaque-sub-123"
    assert linked.refresh_token == "rt-abc"
    assert linked.last_verified is not None


def test_oidc_link_is_not_mistaken_for_a_custom_attribute(kee):
    users = Users(kee)
    user = User(users, detect_user=False)
    user.username = "sso_user"
    user.oidc_link = OidcLink(issuer=ISSUER, sub="opaque-sub-123")
    user.save()

    loaded = User(Users(Kee(password=TEST_DB_PASSWORD)), username="sso_user")

    assert "oidc_link" not in loaded.attribs_cust
    assert "oidc_link" not in users.get_cust_attribs_list()
