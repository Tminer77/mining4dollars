"""The declarative description of what the factory ships.

One TOML file names the app, the platforms it targets, and where each
platform's project lives. Everything downstream — preflight, versioning, the
build plan — reads this and nothing else, so a release is reproducible from a
file in the repository rather than from arguments someone remembered to pass.

Parsing is strict. An unknown key is a typo that would otherwise be silently
ignored until the release it was meant to configure went out wrong.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ANDROID_SECRETS",
    "APPLE_SECRETS",
    "AndroidTarget",
    "AppleTarget",
    "FactorySpec",
    "SpecError",
    "load_spec",
]

SPEC_FILENAME = "factory.toml"

#: Reverse-DNS, the form both stores require. Deliberately stricter than either
#: store enforces: a bundle id cannot be changed once a record exists under it,
#: so a permissive check here buys a permanent mistake.
_BUNDLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9-]*)+$")

#: Semantic version, as both stores display it. Pre-release suffixes are
#: rejected: neither store accepts "1.0.0-beta" as a marketing version.
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

#: Credentials the Apple pipeline cannot run without. An App Store Connect API
#: key is three separate values; missing any one fails at upload, which is the
#: most expensive place in the run to discover it.
APPLE_SECRETS: tuple[str, ...] = (
    "APP_STORE_CONNECT_ISSUER_ID",
    "APP_STORE_CONNECT_KEY_ID",
    "APP_STORE_CONNECT_PRIVATE_KEY",
)

#: Credentials the Android pipeline cannot run without: one to sign the bundle,
#: one to publish it.
ANDROID_SECRETS: tuple[str, ...] = (
    "ANDROID_KEYSTORE_BASE64",
    "ANDROID_KEYSTORE_PASSWORD",
    "ANDROID_KEY_ALIAS",
    "ANDROID_KEY_PASSWORD",
    "PLAY_SERVICE_ACCOUNT_JSON",
)

_APPLE_TRACKS = frozenset({"testflight", "app-store"})
_ANDROID_TRACKS = frozenset({"internal", "alpha", "beta", "production"})
_EXPORT_METHODS = frozenset({"app-store", "ad-hoc", "development", "enterprise"})


class SpecError(Exception):
    """The spec is missing, malformed, or internally inconsistent.

    Carries a remedy in the message where one exists: the spec is edited by
    hand, so an error that only says what is wrong wastes the reader's time.
    """


@dataclass(frozen=True, slots=True)
class AppleTarget:
    """Everything the Apple pipeline needs to know about the project."""

    bundle_id: str
    #: The .xcodeproj or .xcworkspace, relative to the repository root.
    project: str
    scheme: str
    #: "testflight" stops after upload; "app-store" also submits for review.
    track: str = "testflight"
    export_method: str = "app-store"
    #: Passed to xcodebuild -destination. The default builds an unsigned
    #: archive for any watchOS device, which is what a watch app wants.
    destination: str = "generic/platform=watchOS"
    secrets: tuple[str, ...] = APPLE_SECRETS

    @property
    def is_workspace(self) -> bool:
        return self.project.endswith(".xcworkspace")


@dataclass(frozen=True, slots=True)
class AndroidTarget:
    """Everything the Android pipeline needs to know about the project."""

    package: str
    #: Gradle module producing the shippable bundle, e.g. "wear" or "app".
    module: str = "app"
    #: Directory holding gradlew, relative to the repository root.
    project_dir: str = "."
    track: str = "internal"
    secrets: tuple[str, ...] = ANDROID_SECRETS

    @property
    def bundle_task(self) -> str:
        """The Gradle task producing the release AAB."""
        return f":{self.module}:bundleRelease"

    @property
    def test_task(self) -> str:
        return f":{self.module}:testReleaseUnitTest"


@dataclass(frozen=True, slots=True)
class FactorySpec:
    """A whole release: one app, one or both platforms."""

    name: str
    version: str
    root: Path
    apple: AppleTarget | None = None
    android: AndroidTarget | None = None
    #: Extra checks the operator wants enforced, as paths that must exist.
    required_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def platforms(self) -> tuple[str, ...]:
        """The platforms this spec actually targets, in a stable order."""
        return tuple(
            name
            for name, target in (("apple", self.apple), ("android", self.android))
            if target is not None
        )

    def target_for(self, platform: str) -> AppleTarget | AndroidTarget:
        """Look up one platform's target.

        Raises:
            SpecError: if the spec does not target ``platform``.
        """
        target = {"apple": self.apple, "android": self.android}.get(platform)
        if target is None:
            configured = ", ".join(self.platforms) or "none"
            raise SpecError(
                f"This spec does not target {platform!r}. Configured platforms: {configured}. "
                f"Add a [{platform}] section to {SPEC_FILENAME} to ship it."
            )
        return target


def load_spec(root: Path, filename: str = SPEC_FILENAME) -> FactorySpec:
    """Read and validate ``root/filename``.

    Raises:
        SpecError: if the file is missing, is not valid TOML, omits a required
            key, or carries a key the factory does not understand.
    """
    path = root / filename
    if not path.is_file():
        raise SpecError(
            f"No {filename} at {path}. Run `python -m tools.factory init` to write a starting one."
        )

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise SpecError(f"{path} is not valid TOML: {error}") from error

    _reject_unknown(raw, {"app", "apple", "android"}, where=filename)

    app = _required_table(raw, "app")
    _reject_unknown(app, {"name", "version", "required_paths"}, where="[app]")
    name = _string(app, "name", where="[app]")
    version = _string(app, "version", where="[app]")
    if not _SEMVER.match(version):
        raise SpecError(
            f"[app] version {version!r} is not a MAJOR.MINOR.PATCH version. "
            "Both stores reject pre-release suffixes in the marketing version."
        )

    spec = FactorySpec(
        name=name,
        version=version,
        root=root,
        apple=_apple_from(_optional_table(raw, "apple")),
        android=_android_from(_optional_table(raw, "android")),
        required_paths=tuple(_string_list(app, "required_paths", where="[app]")),
    )
    if not spec.platforms:
        raise SpecError(
            f"{filename} targets no platforms. Add an [apple] or [android] section, or both."
        )
    return spec


def _apple_from(table: dict[str, Any] | None) -> AppleTarget | None:
    if table is None:
        return None
    _reject_unknown(
        table,
        {"bundle_id", "project", "scheme", "track", "export_method", "destination", "secrets"},
        where="[apple]",
    )

    bundle_id = _string(table, "bundle_id", where="[apple]")
    if not _BUNDLE_ID.match(bundle_id):
        raise SpecError(
            f"[apple] bundle_id {bundle_id!r} is not reverse-DNS "
            "(letters, digits and hyphens in dot-separated segments, e.g. com.example.myapp). "
            "It cannot be changed once an App Store Connect record exists, so fix it now."
        )

    track = _choice(table, "track", _APPLE_TRACKS, default="testflight", where="[apple]")
    export_method = _choice(
        table, "export_method", _EXPORT_METHODS, default="app-store", where="[apple]"
    )
    if track in {"testflight", "app-store"} and export_method != "app-store":
        raise SpecError(
            f'[apple] track {track!r} requires export_method "app-store", not {export_method!r}. '
            "Only app-store archives are accepted for upload."
        )

    secrets = tuple(_string_list(table, "secrets", where="[apple]")) or APPLE_SECRETS
    return AppleTarget(
        bundle_id=bundle_id,
        project=_string(table, "project", where="[apple]"),
        scheme=_string(table, "scheme", where="[apple]"),
        track=track,
        export_method=export_method,
        destination=_optional_string(
            table, "destination", default="generic/platform=watchOS", where="[apple]"
        ),
        secrets=secrets,
    )


def _android_from(table: dict[str, Any] | None) -> AndroidTarget | None:
    if table is None:
        return None
    _reject_unknown(
        table, {"package", "module", "project_dir", "track", "secrets"}, where="[android]"
    )

    package = _string(table, "package", where="[android]")
    if not _BUNDLE_ID.match(package):
        raise SpecError(
            f"[android] package {package!r} is not reverse-DNS (e.g. com.example.myapp). "
            "It is permanent once the app exists in Play Console."
        )

    secrets = tuple(_string_list(table, "secrets", where="[android]")) or ANDROID_SECRETS
    return AndroidTarget(
        package=package,
        module=_optional_string(table, "module", default="app", where="[android]"),
        project_dir=_optional_string(table, "project_dir", default=".", where="[android]"),
        track=_choice(table, "track", _ANDROID_TRACKS, default="internal", where="[android]"),
        secrets=secrets,
    )


def _required_table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    table = _optional_table(raw, key)
    if table is None:
        raise SpecError(f"{SPEC_FILENAME} has no [{key}] section.")
    return table


def _optional_table(raw: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SpecError(f"[{key}] must be a table, not {type(value).__name__}.")
    return value


def _reject_unknown(table: dict[str, Any], known: set[str], *, where: str) -> None:
    """Fail on any key the factory does not understand.

    A silently ignored key is a setting the operator believes is in force.
    """
    unknown = sorted(set(table) - known)
    if unknown:
        raise SpecError(
            f"{where} has unknown key(s): {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(known))}."
        )


def _string(table: dict[str, Any], key: str, *, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{where} needs a non-empty string {key!r}.")
    return value.strip()


def _optional_string(table: dict[str, Any], key: str, *, default: str, where: str) -> str:
    if key not in table:
        return default
    return _string(table, key, where=where)


def _string_list(table: dict[str, Any], key: str, *, where: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{where} key {key!r} must be a list of strings.")
    return [item.strip() for item in value if item.strip()]


def _choice(
    table: dict[str, Any], key: str, allowed: frozenset[str], *, default: str, where: str
) -> str:
    value = _optional_string(table, key, default=default, where=where)
    if value not in allowed:
        raise SpecError(f"{where} {key} {value!r} must be one of: {', '.join(sorted(allowed))}.")
    return value
