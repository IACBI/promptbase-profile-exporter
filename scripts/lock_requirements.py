"""Regenerate the hash-pinned requirement files that CI installs with --require-hashes.

Usage: python scripts/lock_requirements.py   (needs the network and `packaging`)

The top-level tools are the lines of each file without a "# via" comment; change
their versions there (Dependabot does), then run this to rebuild the rest. Every
dependency is resolved from PyPI's metadata for each environment CI installs in,
with environment markers evaluated for that environment rather than for the
machine running this script, and every file of each version is hashed, so one
file serves Linux, macOS, and Windows alike.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from functools import cache
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
PYTHONS = ("3.10", "3.11", "3.12", "3.13", "3.14")


def linux(python: str) -> dict[str, str]:
    return {
        "python_version": python, "python_full_version": f"{python}.0",
        "sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
        "platform_machine": "x86_64", "implementation_name": "cpython",
        "platform_python_implementation": "CPython", "extra": "",
    }


# Which environments each file is installed in (see .github/workflows).
LOCKS = {
    "requirements-dev.txt": [linux(python) for python in PYTHONS],
    "requirements-release.txt": [linux("3.12")],
}
_PIN = re.compile(r"^([A-Za-z0-9._-]+(?:\[[^\]]*\])?)==([^\s;\\]+)")


@cache
def metadata(name: str, version: str | None = None) -> dict:
    url = f"https://pypi.org/pypi/{name}/{version}/json" if version else \
        f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https
        return json.load(response)


def choose(name: str, specifier: SpecifierSet, python: str) -> str:
    """The newest release that satisfies ``specifier`` and supports ``python``."""
    releases = metadata(name)["releases"]
    for version in sorted(releases, key=Version, reverse=True):
        files = [f for f in releases[version] if not f.get("yanked")]
        parsed = Version(version)
        if not files or parsed.is_prerelease or parsed not in specifier:
            continue
        requires = files[0].get("requires_python") or ""
        if not requires or Version(f"{python}.0") in SpecifierSet(requires):
            return version
    raise SystemExit(f"no release of {name} satisfies {specifier} on Python {python}")


def resolve(top: dict[str, tuple[str, set[str]]], env: dict[str, str]) -> dict[str, tuple]:
    """``{name: (version, via)}`` for ``top`` and everything it needs in ``env``."""
    found: dict[str, tuple[str, set[str]]] = {}
    pending = [(name, SpecifierSet(f"=={version}"), extras, None)
               for name, (version, extras) in top.items()]
    while pending:
        name, specifier, extras, parent = pending.pop()
        key = canonicalize_name(name)
        if key in found:
            version, via = found[key]
            if Version(version) not in specifier:
                raise SystemExit(f"{name} {version} conflicts with {specifier} from {parent}")
            if parent:
                via.add(parent)
            continue
        version = top[key][0] if key in top else choose(name, specifier, env["python_version"])
        found[key] = (version, {parent} if parent else set())
        for line in metadata(name, version)["info"].get("requires_dist") or []:
            requirement = Requirement(line)
            wanted = ["", *sorted(extras)]
            if requirement.marker and not any(
                requirement.marker.evaluate({**env, "extra": extra}) for extra in wanted
            ):
                continue
            pending.append((requirement.name, requirement.specifier,
                            set(requirement.extras), key))
    return found


def hashes(name: str, version: str) -> list[str]:
    return sorted(f["digests"]["sha256"] for f in metadata(name, version)["urls"])


def marker_for(present: list[str]) -> str:
    """A python_version marker for a package needed on only some of PYTHONS."""
    if len(present) == len(PYTHONS):
        return ""
    return " or ".join(f'python_version == "{python}"' for python in present)


def top_level(text: str) -> dict[str, tuple[str, set[str]]]:
    """The pinned entries of a requirements file that have no "# via" comment."""
    top: dict[str, tuple[str, set[str]]] = {}
    for entry in re.split(r"\n(?=[A-Za-z0-9])", text):
        match = _PIN.match(entry)
        if match and "# via" not in entry:
            requirement = Requirement(f"{match[1]}=={match[2]}")
            top[canonicalize_name(requirement.name)] = (match[2], set(requirement.extras))
    return top


def lock(filename: str, envs: list[dict[str, str]]) -> None:
    path = ROOT / filename
    top = top_level(path.read_text(encoding="utf-8"))
    by_env = [resolve(top, env) for env in envs]
    names = sorted({name for resolved in by_env for name in resolved})
    pythons = [env["python_version"] for env in envs]
    lines = [
        "# Generated by scripts/lock_requirements.py: CI installs these with",
        "# --require-hashes. Change a top-level version (a line without \"# via\"),",
        "# then run the script to update the rest and the hashes.",
    ]
    for name in names:
        versions = {resolved[name][0] for resolved in by_env if name in resolved}
        if len(versions) > 1:
            raise SystemExit(f"{name} resolves to several versions: {sorted(versions)}")
        version = versions.pop()
        present = [py for py, resolved in zip(pythons, by_env, strict=True) if name in resolved]
        marker = marker_for(present) if len(envs) == len(PYTHONS) else ""
        if marker:
            Marker(marker)  # fail here, not in pip, if it is malformed
        via = sorted({v for resolved in by_env if name in resolved for v in resolved[name][1]})
        lines.append(f"{name}=={version}" + (f" ; {marker}" if marker else "") + " \\")
        digests = hashes(name, version)
        lines += [f"    --hash=sha256:{digest}" + (" \\" if i < len(digests) - 1 else "")
                  for i, digest in enumerate(digests)]
        if via:
            lines.append(f"    # via {', '.join(via)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{filename}: {len(names)} packages")


def main() -> int:
    for filename, envs in LOCKS.items():
        lock(filename, envs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
