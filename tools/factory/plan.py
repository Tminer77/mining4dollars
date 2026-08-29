"""The ordered commands that turn a project into a store build.

A plan is data, not execution: it can be printed, reviewed, and diffed before
anything runs. That matters more here than in most pipelines, because the
expensive failures are in what the commands *say* — a wrong scheme, an export
method that will not upload — and those are visible by reading.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

from tools.factory.spec import AndroidTarget, AppleTarget, FactorySpec
from tools.factory.steps import Step

__all__ = ["ARTIFACT_DIR", "AppleAuth", "build_plan", "export_options", "write_export_options"]


@dataclass(frozen=True, slots=True)
class AppleAuth:
    """An App Store Connect API key, as xcodebuild wants it on the command line.

    Without this, ``-allowProvisioningUpdates`` has no credential to manage
    signing with and the archive fails on a runner that has never seen the
    team's certificates. The key file is written by the workflow, not by the
    factory: a credential's lifetime should be the job's, not the repository's.
    """

    key_path: str
    key_id: str
    issuer_id: str

    def flags(self) -> tuple[str, ...]:
        return (
            "-authenticationKeyPath",
            self.key_path,
            "-authenticationKeyID",
            self.key_id,
            "-authenticationKeyIssuerID",
            self.issuer_id,
        )


#: Where archives and bundles land, relative to the repository root.
ARTIFACT_DIR = "build/factory"


def build_plan(
    spec: FactorySpec,
    platform: str,
    *,
    version: str,
    build_number: int,
    apple_auth: AppleAuth | None = None,
) -> list[Step]:
    """The steps that ship ``platform`` at ``version`` (``build_number``).

    Args:
        apple_auth: The App Store Connect key xcodebuild signs with. Omitted
            when planning locally, where the developer's own keychain signs.

    Raises:
        SpecError: if the spec does not target ``platform``.
    """
    target = spec.target_for(platform)
    if isinstance(target, AppleTarget):
        return _apple_plan(target, version=version, build_number=build_number, auth=apple_auth)
    return _android_plan(target, version=version, build_number=build_number)


def _apple_plan(
    target: AppleTarget, *, version: str, build_number: int, auth: AppleAuth | None
) -> list[Step]:
    """Archive, export, upload.

    The version and build number are passed as build settings rather than
    written into the project with ``agvtool``: CI then never produces a dirty
    working tree, and two runs of the same commit differ only in the number.
    """
    project_flag = "-workspace" if target.is_workspace else "-project"
    archive = f"{ARTIFACT_DIR}/{target.scheme}.xcarchive"
    export_dir = f"{ARTIFACT_DIR}/export"
    signing = auth.flags() if auth is not None else ()

    return [
        Step(
            name="archive",
            argv=(
                "xcodebuild",
                "archive",
                project_flag,
                target.project,
                "-scheme",
                target.scheme,
                "-destination",
                target.destination,
                "-archivePath",
                archive,
                "-allowProvisioningUpdates",
                *signing,
                f"MARKETING_VERSION={version}",
                f"CURRENT_PROJECT_VERSION={build_number}",
            ),
            secrets=target.secrets,
            macos_only=True,
        ),
        Step(
            name="export",
            argv=(
                "xcodebuild",
                "-exportArchive",
                "-archivePath",
                archive,
                "-exportPath",
                export_dir,
                "-exportOptionsPlist",
                f"{ARTIFACT_DIR}/ExportOptions.plist",
                "-allowProvisioningUpdates",
                *signing,
            ),
            secrets=target.secrets,
            macos_only=True,
        ),
        Step(
            name="upload",
            argv=(
                "xcrun",
                "altool",
                "--upload-app",
                "--type",
                "ios",
                "--file",
                f"{export_dir}/{target.scheme}.ipa",
                # altool reads the key from ~/.appstoreconnect/private_keys/ by
                # id, which is why only the identifiers appear here. There is no
                # shell between the factory and the process, so an argv entry
                # like "$VAR" would be passed through literally, not expanded.
                *(("--apiKey", auth.key_id, "--apiIssuer", auth.issuer_id) if auth else ()),
            ),
            secrets=target.secrets,
            macos_only=True,
        ),
    ]


def _android_plan(target: AndroidTarget, *, version: str, build_number: int) -> list[Step]:
    """Test, then bundle.

    Publishing is left to the workflow's Play action rather than a step here:
    the upload needs a service-account JSON on disk, and writing a credential
    to the workspace is the runner's job to scope, not this plan's.
    """
    gradle = "./gradlew"
    return [
        Step(
            name="unit tests",
            argv=(gradle, target.test_task, "--no-daemon"),
            cwd=target.project_dir,
        ),
        Step(
            name="bundle",
            argv=(
                gradle,
                target.bundle_task,
                "--no-daemon",
                f"-PversionName={version}",
                f"-PversionCode={build_number}",
            ),
            cwd=target.project_dir,
            secrets=target.secrets,
        ),
    ]


def export_options(target: AppleTarget) -> bytes:
    """The ExportOptions.plist the export step reads.

    Generated rather than committed: every value in it is already in the spec,
    and a checked-in copy is one more thing that drifts from it.
    """
    options: dict[str, object] = {
        "method": target.export_method,
        "destination": "export",
        # The API key authenticates the upload; Xcode must not try to sign in.
        "manageAppVersionAndBuildNumber": False,
        "signingStyle": "automatic",
        "uploadSymbols": True,
    }
    return plistlib.dumps(options)


def write_export_options(root: Path, target: AppleTarget) -> Path:
    """Write :func:`export_options` where the export step expects it."""
    path = root / ARTIFACT_DIR / "ExportOptions.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(export_options(target))
    return path
