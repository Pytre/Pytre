"""
Tests for Pytre_Client.wxs - the MSI installer of the client folder, checked against about.py, the build
workflow and the README without running WiX
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from Pytre_Core.about import CLIENT_BASENAME, AppVersion
from Pytre_Core.updater import POLICY_FILE

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
WXS_FILE = ROOT / "Pytre_Client.wxs"
WORKFLOW_FILE = ROOT / ".github" / "workflows" / "build.yml"
README_FILE = ROOT / "README.md"
WIX_NS = {"wix": "http://wixtoolset.org/schemas/v4/wxs"}

# Windows Installer finds the installed versions to replace by this code: changing it would install a new
# version next to the previous one, and break the detection rules of every deployment
UPGRADE_CODE = "{66048ACB-7CC6-4AEE-9BC4-C0F307486C78}"


@pytest.fixture(scope="module")
def wxs_text() -> str:
    return WXS_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def package(wxs_text) -> ET.Element:
    element = ET.fromstring(wxs_text).find("wix:Package", WIX_NS)
    assert element is not None
    return element


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW_FILE.read_text(encoding="utf-8")


# ================================================================================
# Upgrades
# ================================================================================


def test_upgrade_code_never_changes(package):
    assert package.get("UpgradeCode") == UPGRADE_CODE


def test_readme_gives_the_upgrade_code_of_the_installer():
    """Deployments use it in their detection rules"""
    assert UPGRADE_CODE in README_FILE.read_text(encoding="utf-8")


def test_installer_replaces_previous_versions_and_rebuilds_of_the_same_version(package):
    """MSI versions have 3 numbers: a manual build and the stable release of a version share it"""
    major_upgrade = package.find("wix:MajorUpgrade", WIX_NS)

    assert major_upgrade is not None
    assert major_upgrade.get("AllowSameVersionUpgrades") == "yes"


@pytest.mark.parametrize("position, maximum", [(0, 255), (1, 255), (2, 65535)])
def test_current_version_fits_in_a_msi_version(position, maximum):
    """Windows Installer limits: major and minor up to 255, build (our patch) up to 65535"""
    assert 0 <= AppVersion.current().version[position] <= maximum


# ================================================================================
# Installed files
# ================================================================================


def test_installer_uses_the_client_folder_name_and_executable(package):
    """Same folder name as the zip, and the shortcut must target the executable built by the spec"""
    install_folder = package.find(".//wix:Directory[@Id='INSTALLFOLDER']", WIX_NS)
    shortcut = package.find(".//wix:Shortcut", WIX_NS)

    assert install_folder is not None and install_folder.get("Name") == CLIENT_BASENAME
    assert shortcut is not None and shortcut.get("Target") == f"[INSTALLFOLDER]{CLIENT_BASENAME}.exe"


def test_installer_is_installed_for_all_users(package):
    assert package.get("Scope") == "perMachine"


def test_installer_turns_off_the_updates_of_the_installed_client(package):
    """The installed files must stay those of the MSI: deployments install new versions, never the client"""
    install_folder = package.find(".//wix:Directory[@Id='INSTALLFOLDER']", WIX_NS)
    sources = [file.get("Source") for file in install_folder.iterfind(".//wix:File", WIX_NS)]

    assert f"!(bindpath.Templates)\\{POLICY_FILE}" in sources


def test_installer_icon_does_not_depend_on_the_versioned_contents_folder(package):
    icon = package.find("wix:Icon", WIX_NS)

    assert icon is not None and icon.get("SourceFile") == "!(bindpath.Res)\\app.ico"
    assert (ROOT / "Pytre_Core" / "res" / "app.ico").is_file()


# ================================================================================
# Consistency with the build workflow
# ================================================================================


def test_workflow_defines_every_variable_of_the_installer(wxs_text, workflow_text):
    """An undefined preprocessor variable fails the WiX build"""
    used = set(re.findall(r"\$\((\w+)\)", wxs_text))
    defined = set(re.findall(r'-d "(\w+)=', workflow_text))

    assert used
    assert used == defined


def test_workflow_defines_every_bind_path_of_the_installer(wxs_text, workflow_text):
    used = set(re.findall(r"!\(bindpath\.(\w+)\)", wxs_text))
    defined = set(re.findall(r'-bindpath "(\w+)=', workflow_text))

    assert used
    assert used == defined


def test_workflow_bind_paths_to_repository_folders_exist(workflow_text):
    """The client build output isn't in the repository, every other bind path must point to one of its folders"""
    bind_paths = dict(re.findall(r'-bindpath "(\w+)=\$PWD\\([^"]+)"', workflow_text))
    repository_paths = {name: path for name, path in bind_paths.items() if not path.startswith("dist\\")}

    assert repository_paths
    for name, path in repository_paths.items():
        assert (ROOT / Path(*path.split("\\"))).is_dir(), name


def test_workflow_builds_the_installer_from_its_file(workflow_text):
    assert f"wix build {WXS_FILE.name} " in workflow_text


def test_release_publishes_attests_and_checksums_the_client_installer(workflow_text):
    """The MSI is verified like the executables and the zip"""
    checksums_line = next(line for line in workflow_text.splitlines() if "> SHA256SUMS.txt" in line)

    assert "dist/*.msi" in workflow_text
    assert "*.msi" in checksums_line
    assert workflow_text.count("dist/${{ matrix.name }}_*.msi") == 2  # build artifact and attestation
