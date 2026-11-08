"""
Tests for .github/workflows/build.yml - how builds are uploaded, checksummed, described and released. Read as
text: the jobs
are the keys indented by two spaces under `jobs:`, their steps the items indented by six spaces under `steps:`.
Consistency with what each build contains is checked next to its code (test_updater.py, test_installer.py).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

WORKFLOW_FILE = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build.yml"
GATHER_FILES = "find dist -mindepth 2 -type f -exec mv -t dist {} +"


@pytest.fixture(scope="module")
def jobs() -> dict[str, str]:
    """Text of each job of the workflow, by job id"""
    section = WORKFLOW_FILE.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", section, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def steps_of(job: str) -> list[str]:
    """Text of each step of a job"""
    return re.split(r"^      - ", job.split("\n    steps:\n", 1)[1], flags=re.MULTILINE)[1:]


def named_step(job: str, name: str) -> str:
    return next(step for step in steps_of(job) if f"name: {name}\n" in step)


@pytest.fixture(scope="module")
def build_steps(jobs) -> list[str]:
    return steps_of(jobs["build"])


# ================================================================================
# Build artifacts
# ================================================================================


@pytest.mark.parametrize("extension", ["exe", "zip", "msi"])
def test_each_build_is_its_own_artifact_downloadable_as_is(build_steps, extension):
    path = f"path: dist/${{{{ matrix.name }}}}_${{{{ steps.asset.outputs.version }}}}.{extension}\n"
    uploads = [step for step in build_steps if "actions/upload-artifact@" in step and path in step]

    assert len(uploads) == 1
    assert "archive: false" in uploads[0]
    assert "if-no-files-found: error" in uploads[0]


def test_artifact_paths_use_the_version_given_to_the_executable(jobs):
    step = named_step(jobs["build"], "Add version to executable name")

    assert "id: asset" in step
    assert 'echo "version=${version}" >> "$GITHUB_OUTPUT"' in step


@pytest.mark.parametrize("name", ["Build client installer", "Test client installer", "Upload client installer"])
def test_installer_steps_share_one_condition(jobs, name):
    """Built, tested and uploaded together: an upload without the build would fail the job"""
    assert "BUILD_MSI: ${{ matrix.name == 'Pytre_Client'" in jobs["build"]
    assert "if: env.BUILD_MSI == 'true'" in named_step(jobs["build"], name)


@pytest.mark.parametrize("pattern", ["exe", "zip", "msi"])
def test_every_build_is_attested(build_steps, pattern):
    attestation = next(step for step in build_steps if "actions/attest-build-provenance@" in step)

    assert f"dist/${{{{ matrix.name }}}}_*.{pattern}" in attestation


# ================================================================================
# Checksums and release
# ================================================================================


def test_checksums_are_computed_on_every_run_with_read_only_access(jobs):
    """Tags and manual runs alike, so that a manual run checks it before a release depends on it"""
    job = jobs["checksums"]

    assert "needs: build" in job
    assert "\n    if:" not in job
    assert "permissions:" not in job  # contents: read, from the workflow
    assert GATHER_FILES in job
    assert "sha256sum *.exe *.zip *.msi > SHA256SUMS.txt" in job


@pytest.mark.parametrize("job_id", ["checksums", "release"])
def test_downloaded_artifacts_are_kept_as_uploaded(jobs, job_id):
    """The client zip is an artifact of its own: decompressing it would replace it by the client folder"""
    download = next(step for step in steps_of(jobs[job_id]) if "actions/download-artifact@" in step)

    assert "skip-decompress: true" in download
    assert "merge-multiple: true" in download


def test_checksums_are_uploaded_for_the_release(jobs):
    upload = next(step for step in steps_of(jobs["checksums"]) if "actions/upload-artifact@" in step)

    assert "path: dist/SHA256SUMS.txt" in upload
    assert "archive: false" in upload


def test_release_publishes_the_files_checked_by_the_checksums_job(jobs):
    job = jobs["release"]

    assert "needs: [version, checksums, release-notes]" in job
    assert "if: startsWith(github.ref, 'refs/tags/')" in job
    assert "contents: write" in job
    assert GATHER_FILES in job
    assert "sha256sum" not in job
    for published in ("dist/*.exe", "dist/*.zip", "dist/*.msi", "dist/SHA256SUMS.txt"):
        assert published in job, published


def test_release_uses_the_notes_written_from_the_built_files(jobs):
    job = jobs["release"]

    assert "body_path: dist/release_notes.md" in job
    assert "body: |" not in job
    assert "release_notes.md" not in job.split("files: |", 1)[1].split("prerelease:", 1)[0]  # not attached


def test_only_the_release_job_can_write_to_the_repository(jobs):
    writers = [job_id for job_id, job in jobs.items() if "contents: write" in job]

    assert writers == ["release"]


# ================================================================================
# Release notes
# ================================================================================

CHECKSUM = "a" * 64


@pytest.fixture(scope="module")
def write_notes_script(jobs) -> str:
    """Shell script of the step writing the release notes, unindented"""
    step = named_step(jobs["release-notes"], "Write release notes")
    script_lines = step.split("run: |\n", 1)[1].splitlines()
    return "\n".join(line.removeprefix(" " * 10) for line in script_lines)


def test_release_notes_are_written_on_every_run_with_read_only_access(jobs):
    """Like the checksums: a manual run shows them before a release depends on them"""
    job = jobs["release-notes"]
    download = next(step for step in steps_of(job) if "actions/download-artifact@" in step)
    upload = next(step for step in steps_of(job) if "actions/upload-artifact@" in step)

    assert "needs: checksums" in job
    assert "\n    if:" not in job
    assert "permissions:" not in job
    assert "name: SHA256SUMS.txt" in download
    assert "skip-decompress: true" in download
    assert "path: release_notes.md" in upload
    assert "archive: false" in upload


def run_notes_script(script: str, folder: Path, files: list[str] | None) -> subprocess.CompletedProcess:
    """Runs the script in `folder`, with a SHA256SUMS.txt listing `files` - none at all when None"""
    if files is not None:
        lines = [f"{CHECKSUM}  {name}" for name in files]
        (folder / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script], cwd=folder, capture_output=True, text=True, check=False
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash, as the workflow runner")
@pytest.mark.parametrize("with_msi", [True, False], ids=["release", "pre-release"])
def test_release_notes_only_describe_the_built_files(write_notes_script, tmp_path, with_msi):
    files = ["Pytre_Client_2.0.0.exe", "Pytre_Client_2.0.0.zip", "Pytre_Server_2.0.0.exe"]
    files += ["Pytre_Client_2.0.0.msi"] if with_msi else []

    result = run_notes_script(write_notes_script, tmp_path, files)
    notes = (tmp_path / "release_notes.md").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    for pattern in ("Pytre_Client_*.exe", "Pytre_Client_*.zip", "Pytre_Server_*.exe"):
        assert f"- `{pattern}`:" in notes
    assert ("- `Pytre_Client_*.msi`:" in notes) is with_msi
    assert ("The MSI installs a policy file" in notes) is with_msi


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash, as the workflow runner")
def test_release_notes_fail_without_the_checksums_file(write_notes_script, tmp_path):
    """Rather than release empty notes, if the downloaded file lands somewhere else"""
    result = run_notes_script(write_notes_script, tmp_path, None)

    assert result.returncode != 0
    assert not (tmp_path / "release_notes.md").exists()
