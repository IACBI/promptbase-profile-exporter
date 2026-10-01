"""``--config``: read command-line options from a JSON or TOML file.

The file's entries are turned into command-line arguments and put in front of
the real ones, so ``argparse`` validates them exactly as it validates the
command line (choices, number types, exclusive options) and anything typed on
the command line overrides the file.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

# Options whose value is a comma-separated list; a config file may give a list.
COMMA_LIST_OPTIONS = frozenset({"domain", "type", "extra-fields"})
# Never valid in a file: --config would recurse, and the others are not settings.
EXCLUDED_OPTIONS = frozenset({"config", "help", "version"})


class ConfigError(ValueError):
    """The configuration file cannot be used."""


def load_config(path: Path) -> dict[str, Any]:
    """Read a ``.json`` or ``.toml`` file holding a flat table of settings."""
    suffix = path.suffix.lower()
    if suffix not in {".json", ".toml"}:
        raise ConfigError(f"{path}: the configuration file must be .json or .toml")
    try:
        if suffix == ".toml":
            try:
                # The standard library has a TOML reader from Python 3.11. Loaded by
                # name so the project's 3.10 type-checking target need not know it.
                tomllib = importlib.import_module("tomllib")
            except ModuleNotFoundError as exc:
                raise ConfigError(
                    f"{path}: TOML needs Python 3.11 or newer; use a .json configuration file"
                ) from exc
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        else:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # tomllib.TOMLDecodeError is a ValueError
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the configuration must be a table of settings")
    return data


def split_config(
    config: Mapping[str, Any],
    parser: argparse.ArgumentParser,
) -> tuple[list[str], list[str]]:
    """Turn a configuration into ``(arguments, profiles)``.

    ``arguments`` are command-line tokens for every setting except ``profiles``,
    which is returned separately because a profile is positional and the
    command line's own profiles replace the file's.
    """
    known = {
        option[2:]: action
        for option, action in parser._option_string_actions.items()
        if option.startswith("--") and option[2:] not in EXCLUDED_OPTIONS
    }
    arguments: list[str] = []
    profiles: list[str] = []
    seen: dict[str, str] = {}
    for key, value in config.items():
        if key == "profiles":
            profiles = _profiles(value)
            continue
        option = key.replace("_", "-")
        if option in EXCLUDED_OPTIONS:
            raise ConfigError(f"{key!r} cannot be set in a configuration file")
        if option not in known:
            raise ConfigError(f"unknown setting {key!r}")
        if option in seen:
            raise ConfigError(f"{key!r} and {seen[option]!r} are the same setting")
        seen[option] = key
        arguments.extend(_tokens(key, option, known[option], value))
    return arguments, profiles


def without_overridden(
    arguments: Sequence[str],
    command_line: Sequence[str],
    parser: argparse.ArgumentParser,
) -> list[str]:
    """``arguments`` from a file, less those a command-line option excludes.

    The command line wins, but argparse refuses two options of one mutually
    exclusive group (``--free-only`` from the file, ``--paid-only`` typed): the
    file's option gives way instead.
    """
    typed = {action for token in command_line if (action := _action(parser, token))}
    dropped: set[argparse.Action] = set()
    for group in parser._mutually_exclusive_groups:
        chosen = typed & set(group._group_actions)
        if chosen:
            dropped |= set(group._group_actions) - chosen
    return [token for token in arguments if _action(parser, token) not in dropped]


def _action(parser: argparse.ArgumentParser, token: str) -> argparse.Action | None:
    """The option a ``--name`` or ``--name=value`` token (or a prefix of it) sets."""
    if not token.startswith("--"):
        return None
    name = token.split("=", 1)[0]
    exact = parser._option_string_actions.get(name)
    if exact is not None:
        return exact
    # argparse accepts an unambiguous prefix (--paid for --paid-only).
    matches = {action for option, action in parser._option_string_actions.items()
               if option.startswith(name)}
    return matches.pop() if len(matches) == 1 else None


def _profiles(value: Any) -> list[str]:
    if value is None:  # like every other setting, null leaves it unset
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ConfigError("'profiles' must be a profile or a list of profiles")
    return items


def _tokens(key: str, option: str, action: argparse.Action, value: Any) -> list[str]:
    flag = f"--{option}"
    if value is None:
        return []
    if action.nargs == 0:  # an on/off flag
        if not isinstance(value, bool):
            raise ConfigError(f"{key!r} must be true or false")
        return [flag] if value else []
    # The --option=value form, not two tokens: a value that starts with a hyphen
    # (a directory called "-exports") would otherwise be read as another option.
    if isinstance(action, argparse._AppendAction):
        values = value if isinstance(value, list) else [value]
        return [f"{flag}={_scalar(key, item)}" for item in values]
    if isinstance(value, list) and option in COMMA_LIST_OPTIONS:
        return [f"{flag}={','.join(_scalar(key, item) for item in value)}"]
    return [f"{flag}={_scalar(key, value)}"]


def _scalar(key: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ConfigError(f"{key!r} must be a text or number value, not {type(value).__name__}")
    return str(value)


def check_arguments(
    make_parser: Callable[[], argparse.ArgumentParser],
    arguments: Sequence[str],
    path: Path,
) -> None:
    """Parse the file's arguments on their own, so a bad value is reported against the file.

    Without this, ``"format": "yaml"`` would surface as a bare argparse error
    about ``-f/--format``, with nothing to say it came from the configuration.
    """
    parser = make_parser()

    def fail(message: str) -> NoReturn:
        raise ConfigError(f"{path}: {message}")

    parser.error = fail  # type: ignore[method-assign]
    parser.parse_args(list(arguments))


def find_config_path(parser: argparse.ArgumentParser, argv: Sequence[str]) -> Path | None:
    """The ``--config`` path on the command line, if any, before the file is applied.

    Asks the real parser rather than a look-alike, so a spelling the real parser
    accepts (``--config=f``, or an unambiguous abbreviation such as ``--conf f``)
    is found too; a separate finder that disagreed would leave the file unread
    without a word.
    """
    known, _ = parser.parse_known_intermixed_args(list(argv))
    config = known.config
    return config if isinstance(config, Path) else None
