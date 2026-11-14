"""
Enterprise policy file: `Pytre.policy.json`, next to the client executable, where a managed deployment restricts
what the client may do. Each policy reads its own key of the file. An unreadable or invalid file never loosens
anything: every policy is then as restrictive as it can be.
"""

import json
import logging
from enum import IntEnum
from pathlib import Path
from typing import Self

from Pytre_Core.app_folders import get_install_folder
from Pytre_Core.utils import detect_encoding, log as _log


POLICY_FILE: str = "Pytre.policy.json"  # enterprise policy, next to the executable


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class _Policy(IntEnum):
    """Base of the policies read from POLICY_FILE. A policy's values go from the least restrictive (the lowest) to the
    most restrictive (the highest): it gets the lowest without the file or without its key, the highest when the file
    can't be read or holds an unknown value."""

    @classmethod
    def policy_key(cls) -> str:
        """Key of POLICY_FILE holding this policy."""
        raise NotImplementedError

    @classmethod
    def _from_value(cls, value: str) -> Self:
        """Policy from its name (case insensitive). Raises ValueError otherwise."""
        try:
            return cls[str(value).upper()]
        except KeyError:
            raise ValueError(f"Unknown {cls.policy_key()}: {value}") from None

    @classmethod
    def load(cls, folder: Path | None = None) -> Self:
        """Policy set by POLICY_FILE in `folder` (the install folder by default). Never raises."""
        key = cls.policy_key()
        file_src = (folder or get_install_folder()) / POLICY_FILE

        if not file_src.exists():
            return min(cls)

        try:
            encoding = detect_encoding(file_src)
            if not encoding:
                raise ValueError("unknown encoding")

            with open(file_src, mode="r", encoding=encoding) as f:
                json_dict = json.load(f)

            if not isinstance(json_dict, dict):
                raise ValueError("root must be a JSON object")
            elif key not in json_dict:
                policy = min(cls)
                log(f"No {key} defined, {policy.name.lower()}.", logging.INFO)
            else:
                policy = cls._from_value(json_dict[key])
        except (OSError, ValueError) as e:
            policy = max(cls)
            log(f"Invalid {key} in {file_src.name}, {policy.name.lower()}: {e}", logging.WARNING)

        log(f"{key}: {policy.name.lower()} ({file_src})", logging.INFO)
        return policy


class UpdatePolicy(_Policy):
    """Updates allowed by the enterprise policy file."""

    ALLOWED = 1  # the user pref decides on the startup check, manual check available
    DISABLED = 2  # no check at all, no network call

    @classmethod
    def policy_key(cls) -> str:
        return "updates"


class PlainHttpRemotesPolicy(_Policy):
    """Plain HTTP to remotes allowed by the enterprise policy file."""

    ALLOWED = 1  # plain HTTP to a private network address, or to any address the user explicitly allows
    DISABLED = 2  # plain HTTP only to the machine itself (loopback), whatever the user allows

    @classmethod
    def policy_key(cls) -> str:
        return "plain_http_remotes"
