"""
Tests for .github/workflows/build.yml - how builds are uploaded, checksummed and released. Read as text: the jobs
are the keys indented by two spaces under `jobs:`, their steps the items indented by six spaces under `steps:`.
Consistency with what each build contains is checked next to its code (test_updater.py, test_installer.py).
"""

import re
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

    assert "needs: [version, checksums]" in job
    assert "if: startsWith(github.ref, 'refs/tags/')" in job
    assert "contents: write" in job
    assert GATHER_FILES in job
    assert "sha256sum" not in job
    for published in ("dist/*.exe", "dist/*.zip", "dist/*.msi", "dist/SHA256SUMS.txt"):
        assert published in job, published


def test_only_the_release_job_can_write_to_the_repository(jobs):
    writers = [job_id for job_id, job in jobs.items() if "contents: write" in job]

    assert writers == ["release"]
