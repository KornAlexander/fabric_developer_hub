"""
Bootstrap / smoke tests for the ManifestPackage pipeline.

These tests exist because the full unit+API suite (150+ tests) went green while
the real app was broken: opening the Fabric workspace returned
    "❌ ManifestPackage not found. Run: cd Backend/python && python
     tools/manifest_package_generator.py --version 1.0.0"
in the network tab and frontend logs.

Root cause: the frontend webpack dev-server referenced the .nupkg at
    ../../Backend/python/bin/Debug/ManifestPackage.1.0.0.nupkg
but the generator writes it at
    ../../Backend/bin/Debug/ManifestPackage.1.0.0.nupkg
(the `python/` subfolder no longer exists after the backend was renamed).

Every existing test was happy because:
  * Unit tests mock the generator.
  * API tests hit FastAPI directly and never touch the frontend.
  * No test asserted the contract between "where the generator writes" and
    "where the frontend reads".

The tests below invoke the REAL generator (no mocks on the system under test),
inspect the produced artifact, and assert the path contract with webpack so
this exact regression fails loudly.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

# ───────────────────────── Paths / constants ──────────────────────────

# This file lives at: Developer Hub/Backend/tests/integration/test_manifest_package_bootstrap.py
# So the Backend root is parents[2], and the "Developer Hub" root is parents[3].
BACKEND_ROOT: Path = Path(__file__).resolve().parents[2]
DEV_HUB_ROOT: Path = Path(__file__).resolve().parents[3]
FRONTEND_ROOT: Path = DEV_HUB_ROOT / "Frontend"
GENERATOR_SCRIPT: Path = BACKEND_ROOT / "tools" / "manifest_package_generator.py"
WEBPACK_CONFIG: Path = FRONTEND_ROOT / "tools" / "webpack.config.js"
MANIFEST_DIR: Path = BACKEND_ROOT / "manifest"

# The generator's documented output layout (mirrored in docker-compose volume
# mount `manifest-pkg:/app/../Backend/bin/Debug`). If either side of this
# contract changes, this constant and the webpack path must change together.
EXPECTED_PACKAGE_VERSION = "1.0.0"
EXPECTED_PACKAGE_NAME = f"ManifestPackage.{EXPECTED_PACKAGE_VERSION}.nupkg"
EXPECTED_OUTPUT_SUBDIR = Path("bin") / "Debug"

# Required env vars with deterministic test values. The generator fails hard
# if these are missing, which is the correct production behavior.
REQUIRED_ENV = {
    "WORKLOAD_NAME": "Org.TestWorkload",
    "CLIENT_ID": "11111111-1111-1111-1111-111111111111",
    "AUDIENCE": "api://localdevinstance/22222222-2222-2222-2222-222222222222/Org.TestWorkload/test",
}


# ───────────────────────── Fixtures ───────────────────────────────────


@pytest.fixture
def manifest_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set the env vars the generator requires. Deterministic values so we
    can assert them in the produced manifest."""
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    return dict(REQUIRED_ENV)


@pytest.fixture
def generated_package(manifest_env: dict[str, str], tmp_path: Path) -> Iterator[Path]:
    """Invoke the REAL generator as a subprocess (no mocks) and yield the
    produced .nupkg path. Using a subprocess guarantees we exercise the same
    entrypoint docker-compose runs.
    """
    output_dir = tmp_path / "bin" / "Debug"
    cmd = [
        sys.executable,
        str(GENERATOR_SCRIPT),
        "--version",
        EXPECTED_PACKAGE_VERSION,
        "--project-root",
        str(BACKEND_ROOT),
        "--output-dir",
        str(output_dir),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **manifest_env},
    )
    # Surface generator output so failures are diagnosable.
    if proc.returncode != 0:
        pytest.fail(
            "manifest_package_generator.py exited "
            f"{proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    produced = output_dir / EXPECTED_PACKAGE_NAME
    yield produced


# ─────────────────── Layer 1: Smoke & Bootstrap ────────────────────────


@pytest.mark.integration
@pytest.mark.smoke
class TestManifestPackageGenerator:
    """The generator is a build step. These tests treat it as one."""

    def test_generator_produces_file_at_expected_name_and_version(
        self, generated_package: Path
    ) -> None:
        # Arrange done by fixture.

        # Act — inspect the produced artifact.
        exists = generated_package.is_file()
        name = generated_package.name
        size = generated_package.stat().st_size if exists else 0

        # Assert — observable outputs a caller (DevGateway / webpack) cares about.
        assert exists, f"expected generator to write {generated_package}"
        assert name == EXPECTED_PACKAGE_NAME, (
            f"package filename contract broken; frontend and DevGateway both "
            f"hardcode {EXPECTED_PACKAGE_NAME}, generator produced {name}"
        )
        assert size > 1024, (
            f"package is suspiciously small ({size} bytes); probably empty zip"
        )

    def test_generated_package_is_a_valid_zip_with_fabric_entries(
        self, generated_package: Path
    ) -> None:
        assert zipfile.is_zipfile(generated_package), (
            "nupkg must be a valid zip — DevGateway rejects non-zip payloads"
        )

        with zipfile.ZipFile(generated_package) as zf:
            names = set(zf.namelist())
            bad = [n for n in zf.testzip() or []]

        assert not bad, f"corrupt zip entries: {bad}"
        # Fabric DevGateway only accepts entries under BE/ and FE/ (see
        # generator source: "Fabric DevGateway only accepts entries under
        # BE/ and FE/ directories").
        assert "BE/WorkloadManifest.xml" in names, (
            f"missing BE/WorkloadManifest.xml; got {sorted(names)}"
        )
        assert "BE/AgentHubItem.xml" in names, (
            f"missing BE/AgentHubItem.xml; got {sorted(names)}"
        )
        # No stray .nuspec — that would make DevGateway reject the package.
        assert not any(n.endswith(".nuspec") for n in names), (
            "Fabric rejects packages that contain .nuspec entries"
        )

    def test_workload_manifest_has_env_substitutions_applied(
        self, generated_package: Path, manifest_env: dict[str, str]
    ) -> None:
        with zipfile.ZipFile(generated_package) as zf:
            with zf.open("BE/WorkloadManifest.xml") as fh:
                xml_bytes = fh.read()

        xml_text = xml_bytes.decode("utf-8")

        # Placeholders must have been replaced — shipping a manifest that
        # still contains ${WORKLOAD_NAME} is exactly the kind of "it built
        # but it's broken" defect this suite exists to catch.
        assert "${WORKLOAD_NAME}" not in xml_text
        assert "${CLIENT_ID}" not in xml_text
        assert "${AUDIENCE}" not in xml_text
        assert manifest_env["WORKLOAD_NAME"] in xml_text, (
            f"expected WORKLOAD_NAME={manifest_env['WORKLOAD_NAME']} in rendered manifest"
        )
        assert manifest_env["CLIENT_ID"] in xml_text
        assert manifest_env["AUDIENCE"] in xml_text

        # And the XML must still parse after substitution.
        ET.fromstring(xml_text)  # raises ParseError if we corrupted it

    def test_generator_fails_with_clear_message_when_required_env_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange — scrub the required vars.
        for var in REQUIRED_ENV:
            monkeypatch.delenv(var, raising=False)

        # Act — run the generator with no substitutions available.
        output_dir = tmp_path / "bin" / "Debug"
        proc = subprocess.run(
            [
                sys.executable,
                str(GENERATOR_SCRIPT),
                "--version",
                EXPECTED_PACKAGE_VERSION,
                "--project-root",
                str(BACKEND_ROOT),
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
            # Explicit env: drop the required vars even if the caller has them.
            env={
                k: v
                for k, v in os.environ.items()
                if k not in REQUIRED_ENV
            },
        )

        # Assert — non-zero exit and an actionable message naming the
        # missing vars. Not a 500-style crash with a raw traceback.
        assert proc.returncode != 0, (
            "generator must fail when required env vars are missing; "
            "shipping a manifest with unresolved ${...} placeholders is worse "
            "than a hard build failure"
        )
        combined = (proc.stdout + proc.stderr).lower()
        assert "env var" in combined or "environment" in combined or ".env" in combined, (
            f"expected actionable missing-env message, got:\n{proc.stdout}\n{proc.stderr}"
        )
        for var in REQUIRED_ENV:
            assert var in proc.stdout + proc.stderr, (
                f"expected {var} to be named in the error output"
            )


# ─────────────── Layer 2: Contract between generator and frontend ──────
#
# This is the regression test for the exact bug that shipped. It asserts the
# frontend webpack dev-server fetches from the same relative path the
# generator writes to. If either side drifts, this test fails.


@pytest.mark.integration
@pytest.mark.smoke
class TestFrontendManifestPathContract:
    """The frontend dev-server path must match the generator's output path."""

    @pytest.fixture
    def webpack_config_text(self) -> str:
        assert WEBPACK_CONFIG.is_file(), f"missing {WEBPACK_CONFIG}"
        return WEBPACK_CONFIG.read_text(encoding="utf-8")

    def test_webpack_manifest_path_does_not_reference_removed_python_subdir(
        self, webpack_config_text: str
    ) -> None:
        # The `Backend/python/` subfolder was removed when the backend was
        # renamed. A path referencing it will always 404 in the container.
        assert "Backend/python/bin" not in webpack_config_text, (
            "webpack.config.js still references the removed Backend/python/ "
            "subfolder — this is the exact ManifestPackage-not-found bug"
        )

    def test_webpack_manifest_path_matches_generator_output_path(
        self, webpack_config_text: str
    ) -> None:
        # Extract every path.resolve(__dirname, '...nupkg') literal and
        # resolve it against webpack.config.js's directory.
        pattern = re.compile(
            r"path\.resolve\s*\(\s*__dirname\s*,\s*['\"]([^'\"]+\.nupkg)['\"]\s*\)"
        )
        matches = pattern.findall(webpack_config_text)

        assert matches, (
            "webpack.config.js no longer contains a path.resolve(...) for the "
            ".nupkg — update this test if the serving strategy changed"
        )

        webpack_dir = WEBPACK_CONFIG.parent
        resolved = [(webpack_dir / rel).resolve() for rel in matches]

        expected_output_dir = (BACKEND_ROOT / EXPECTED_OUTPUT_SUBDIR).resolve()

        for path in resolved:
            assert path.name == EXPECTED_PACKAGE_NAME, (
                f"webpack serves {path.name} but generator produces "
                f"{EXPECTED_PACKAGE_NAME}"
            )
            assert path.parent == expected_output_dir, (
                f"webpack reads from {path.parent} but generator writes to "
                f"{expected_output_dir} — this mismatch is the "
                "ManifestPackage-not-found regression"
            )

    def test_webpack_error_message_directs_user_to_a_real_directory(
        self, webpack_config_text: str
    ) -> None:
        # The old message told users to `cd Backend/python && ...`. That
        # path does not exist. Guard against a regression where the path
        # was fixed but the actionable message wasn't.
        backend_dir = DEV_HUB_ROOT / "Backend"
        python_subdir = backend_dir / "python"

        # Look for the error string emitted on 404. The emitter is
        # ``webpackConsole.error(...)`` — a small wrapper defined in
        # ``webpack.config.js`` that prefixes lines with a distinct glyph
        # so operators can tell workload-server errors from the
        # ``webpack-dev-server`` chrome. Accept both the wrapper and the
        # plain ``console.error`` so the test does not lock us into one
        # specific helper name.
        console_err_match = re.search(
            r"(?:webpackC|c)onsole\.error\(\s*`([^`]*ManifestPackage[^`]*)`",
            webpack_config_text,
        )
        assert console_err_match, (
            "webpack 404 handler no longer logs a ManifestPackage message — "
            "keep it so operators get an actionable hint in frontend logs"
        )
        msg = console_err_match.group(1)
        assert backend_dir.is_dir(), f"sanity check: {backend_dir} should exist"
        assert not python_subdir.exists(), (
            f"{python_subdir} was removed — error message must not send "
            f"users there"
        )
        assert "Backend/python" not in msg, (
            "error message still tells users to cd into the removed "
            "Backend/python directory"
        )

    def test_generator_output_dir_matches_docker_compose_volume_source(self) -> None:
        # The docker-compose volume binds the generated package into the
        # frontend container at `/Backend/bin/Debug`. If the generator ever
        # changed its default output dir, the frontend would silently 404
        # again. Pin the contract here.
        compose_path = DEV_HUB_ROOT / "docker-compose.yaml"
        text = compose_path.read_text(encoding="utf-8")
        assert "Backend/bin/Debug" in text or "bin/Debug" in text, (
            "docker-compose volume no longer mounts Backend/bin/Debug; "
            "generator output location and frontend read path must stay in sync"
        )
        assert "Backend/python/bin" not in text, (
            "docker-compose references removed Backend/python/ path"
        )


# ─────────────── Layer 2b: Generator module import sanity ──────────────
#
# Pure smoke test: the generator script must at least be importable and
# expose the class. Catches syntax errors that would silently break the
# manifest-generator container at `docker compose up` time.


@pytest.mark.integration
@pytest.mark.smoke
def test_manifest_generator_module_imports_cleanly() -> None:
    spec = importlib.util.spec_from_file_location(
        "manifest_package_generator_under_test", GENERATOR_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)  # raises on syntax / top-level errors

    assert hasattr(module, "ManifestPackageGenerator"), (
        "public class ManifestPackageGenerator removed — CLI and docker "
        "manifest-generator step both depend on it"
    )
