"""
Tests for Pytre_Core/about.py - AppVersion parsing, formatting and ordering
"""

import dataclasses

import pytest

from Pytre_Core import about
from Pytre_Core.about import AppStage, AppVersion, expected_git_tag, full_version


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def current_version(monkeypatch):
    """Sets the current version constants of about.py for the duration of a test"""

    def set_current(version: tuple[int, int, int], stage: AppStage, stage_number: int = 0):
        monkeypatch.setattr(about, "APP_VERSION", version)
        monkeypatch.setattr(about, "APP_STAGE", stage)
        monkeypatch.setattr(about, "APP_STAGE_NUMBER", stage_number)

    return set_current


# ================================================================================
# Parsing
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2.0.0", AppVersion((2, 0, 0), AppStage.RELEASE, 0)),
        ("2.0.0-alpha", AppVersion((2, 0, 0), AppStage.ALPHA, 0)),
        ("2.0.0-beta.2", AppVersion((2, 0, 0), AppStage.BETA, 2)),
        ("2.0.10-rc.1", AppVersion((2, 0, 10), AppStage.RC, 1)),
        ("v.2.0.0-beta.2", AppVersion((2, 0, 0), AppStage.BETA, 2)),
        ("v.2.0.0", AppVersion((2, 0, 0), AppStage.RELEASE, 0)),
        ("2.0.0-BETA.2", AppVersion((2, 0, 0), AppStage.BETA, 2)),
        ("  2.0.0-rc.1  ", AppVersion((2, 0, 0), AppStage.RC, 1)),
    ],
)
def test_build_from_string_parses_versions_and_git_tags(text, expected):
    assert AppVersion.build_from_string(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    ["", "2.0", "2.0.0.1", "v2.0.0", "2.0.0_beta.2", "2.0.0-beta.", "2.0.0-beta-2", "a.b.c", "2.0.0 beta 2"],
)
def test_build_from_string_rejects_invalid_formats(text):
    with pytest.raises(ValueError, match="Invalid version format"):
        AppVersion.build_from_string(text)


@pytest.mark.unit
def test_build_from_string_rejects_unknown_stage():
    with pytest.raises(ValueError, match="Unknown AppStage"):
        AppVersion.build_from_string("2.0.0-gamma.1")


# ================================================================================
# AppStage
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [
        (AppStage.BETA, AppStage.BETA),
        ("RC", AppStage.RC),
        ("beta", AppStage.BETA),
        ("Release", AppStage.RELEASE),
        (1, AppStage.ALPHA),
        (4, AppStage.RELEASE),
    ],
)
def test_app_stage_from_value_accepts_a_stage_its_name_or_its_number(value, expected):
    assert AppStage.from_value(value) is expected


@pytest.mark.unit
@pytest.mark.parametrize("value", ["gamma", "", 0, 5, None, 2.0])
def test_app_stage_from_value_rejects_unknown_values(value):
    with pytest.raises(ValueError):
        AppStage.from_value(value)


@pytest.mark.unit
def test_app_stages_are_ordered_from_least_to_most_stable():
    assert AppStage.ALPHA < AppStage.BETA < AppStage.RC < AppStage.RELEASE


# ================================================================================
# Formatting
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "version, expected",
    [
        (AppVersion((2, 0, 0)), "2.0.0"),
        (AppVersion((2, 0, 0), AppStage.ALPHA), "2.0.0-alpha"),
        (AppVersion((2, 0, 0), AppStage.BETA, 2), "2.0.0-beta.2"),
        (AppVersion((1, 10, 3), AppStage.RC, 1), "1.10.3-rc.1"),
    ],
)
def test_str_formats_the_version(version, expected):
    assert str(version) == expected


@pytest.mark.unit
def test_str_ignores_the_stage_number_of_a_release():
    assert str(AppVersion((2, 0, 0), AppStage.RELEASE, 3)) == "2.0.0"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["2.0.0", "2.0.0-alpha", "2.0.0-beta.2", "2.0.10-rc.1"])
def test_str_and_build_from_string_round_trip(text):
    assert str(AppVersion.build_from_string(text)) == text


@pytest.mark.unit
def test_base_version_keeps_only_the_numbers():
    assert AppVersion((2, 0, 10), AppStage.BETA, 2).base_version == "2.0.10"


# ================================================================================
# Ordering
# ================================================================================


@pytest.mark.unit
def test_versions_are_ordered_by_numbers_then_stage_then_stage_number():
    ordered = [
        "1.9.9",
        "2.0.0-alpha",
        "2.0.0-alpha.1",
        "2.0.0-beta.2",
        "2.0.0-beta.10",
        "2.0.0-rc.1",
        "2.0.0",
        "2.0.1-alpha",
        "2.0.10",
        "10.0.0",
    ]
    versions = [AppVersion.build_from_string(text) for text in ordered]

    assert sorted(reversed(versions)) == versions
    assert all(earlier < later for earlier, later in zip(versions, versions[1:]))


@pytest.mark.unit
def test_release_is_newer_than_its_prereleases():
    release = AppVersion.build_from_string("2.0.0")

    assert release > AppVersion.build_from_string("2.0.0-rc.9")
    assert release >= AppVersion.build_from_string("2.0.0")
    assert max([release, AppVersion.build_from_string("2.0.0-beta.2")]) == release


@pytest.mark.unit
def test_tag_and_version_strings_give_equal_and_hashable_versions():
    from_tag = AppVersion.build_from_string("v.2.0.0-beta.2")
    from_version = AppVersion.build_from_string("2.0.0-beta.2")

    assert from_tag == from_version
    assert len({from_tag, from_version}) == 1


@pytest.mark.unit
def test_app_version_is_immutable():
    version = AppVersion((2, 0, 0))

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(version, "stage", AppStage.BETA)


# ================================================================================
# Properties
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2.0.0-alpha", (2, 0, 0, 100)),
        ("2.0.0-alpha.3", (2, 0, 0, 103)),
        ("2.0.0-beta.2", (2, 0, 0, 202)),
        ("2.0.0-rc.1", (2, 0, 0, 301)),
        ("2.0.0", (2, 0, 0, 400)),
    ],
)
def test_binary_version_encodes_the_stage_in_the_fourth_number(text, expected):
    assert AppVersion.build_from_string(text).binary_version == expected


@pytest.mark.unit
def test_binary_versions_follow_the_version_order():
    texts = ["2.0.0-alpha", "2.0.0-beta.2", "2.0.0-beta.10", "2.0.0-rc.1", "2.0.0"]
    binary_versions = [AppVersion.build_from_string(text).binary_version for text in texts]

    assert binary_versions == sorted(binary_versions)


@pytest.mark.unit
@pytest.mark.parametrize(
    "stage, expected",
    [(AppStage.ALPHA, True), (AppStage.BETA, True), (AppStage.RC, True), (AppStage.RELEASE, False)],
)
def test_is_prerelease_for_every_stage_but_release(stage, expected):
    assert AppVersion((2, 0, 0), stage).is_prerelease is expected


# ================================================================================
# Current version
# ================================================================================


@pytest.mark.unit
def test_current_is_built_from_the_module_constants(current_version):
    current_version((3, 1, 4), AppStage.RC, 2)

    assert AppVersion.current() == AppVersion((3, 1, 4), AppStage.RC, 2)


@pytest.mark.unit
def test_current_constants_form_a_valid_version():
    """Guards the values committed in about.py: the release workflow parses them back from the git tag"""
    assert AppVersion.build_from_string(expected_git_tag()) == AppVersion.current()


@pytest.mark.unit
@pytest.mark.parametrize(
    "version, stage, stage_number, expected_version, expected_tag",
    [
        ((2, 0, 0), AppStage.BETA, 2, "2.0.0-beta.2", "v.2.0.0-beta.2"),
        ((2, 0, 0), AppStage.ALPHA, 0, "2.0.0-alpha", "v.2.0.0-alpha"),
        ((2, 1, 0), AppStage.RELEASE, 0, "2.1.0", "v.2.1.0"),
    ],
)
def test_full_version_and_expected_git_tag(current_version, version, stage, stage_number, expected_version, expected_tag):
    current_version(version, stage, stage_number)

    assert full_version() == expected_version
    assert expected_git_tag() == expected_tag
