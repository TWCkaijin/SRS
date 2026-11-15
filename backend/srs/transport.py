"""Executor-only credential and transport helpers; no automatic discovery."""

import hmac
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import asyncssh
from dotenv import dotenv_values


class TransportError(ValueError):
    """A failed closed precondition, before dispatch."""


def failure(message: str, *, unknown: bool = False, exit_code: int | None = None) -> dict:
    return {"exit_code": exit_code, "signal": None, "timed_out": False,
            "unknown": unknown, "outputs": {}, "error": message}


def validate_context(mode: str, step: dict, context: dict) -> dict:
    if mode not in ("local-slurm", "remote-slurm") or context.get("mode") != mode:
        raise TransportError("Real transport requires an explicitly matching Slurm mode")
    host = context.get("host") or {}
    if not host.get("enabled", False):
        raise TransportError("Host is excluded from execution")
    if step.get("host_id") != host.get("id"):
        raise TransportError("Step host identity does not match executor inventory")
    for field in ("hostname", "ssh_username", "alias"):
        if not isinstance(host.get(field), str) or not host[field]:
            raise TransportError("Missing public host field: " + field)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", host["ssh_username"]):
        raise TransportError("Invalid SSH username")
    if not isinstance(host.get("port", 22), int) or not 1 <= host.get("port", 22) <= 65535:
        raise TransportError("Invalid SSH port")
    pin = host.get("host_key_sha256", "")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}=?", pin):
        raise TransportError("A verified SHA256 SSH host-key fingerprint is required")
    return host


@dataclass(frozen=True, repr=False)
class HostCredentials:
    username: str
    password: str
    sudo_password: str | None

    def __repr__(self):
        return "HostCredentials(username=" + repr(self.username) + ", credentials=[REDACTED])"


class CredentialStore:
    """Explicit .env.hosts file, no inherited environment or cross-host fallback."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path is not None else None

    @staticmethod
    def prefix(host: dict) -> str:
        alias = host.get("alias", "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", alias):
            raise TransportError("Host alias cannot form a credential reference")
        return "HOST_" + alias.upper()

    def read(self) -> dict[str, str]:
        if self.path is None:
            raise TransportError("Executor credentials file is not configured")
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise TransportError("Credentials file must be an owned regular file with mode 0600")
            values = dotenv_values(self.path, interpolate=False)
        except OSError as exc:
            raise TransportError("Executor credentials file is unavailable") from exc
        return {key: value for key, value in values.items() if value is not None}

    def for_host(self, host: dict, *, sudo: bool = False) -> HostCredentials:
        values = self.read()
        prefix = self.prefix(host)
        username = values.get(prefix + "_USERNAME")
        password = values.get(prefix + "_SSH_PASSWORD")
        if username != host["ssh_username"]:
            raise TransportError("Credential username differs from the approved host identity")
        if not password:
            raise TransportError("Missing host-specific SSH password reference: " + prefix + "_SSH_PASSWORD")
        sudo_password = values.get(prefix + "_SUDO_PASSWORD") if sudo else None
        if sudo_password and ("\n" in sudo_password or "\r" in sudo_password):
            raise TransportError("Sudo password cannot contain a newline")
        return HostCredentials(username, password, sudo_password)

    def resolve_environment(self, environment: dict) -> tuple[dict[str, str], list[str]]:
        resolved = {}
        secrets = []
        values = None
        for key, value in environment.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise TransportError("Invalid environment variable name")
            if isinstance(value, dict) and set(value) == {"secret_ref"}:
                reference = value["secret_ref"]
                if not isinstance(reference, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", reference):
                    raise TransportError("Invalid secret reference")
                if values is None:
                    values = self.read()
                if reference not in values:
                    raise TransportError("Missing executor secret reference: " + reference)
                resolved[key] = values[reference]
                secrets.append(values[reference])
            elif isinstance(value, str) and "\0" not in value:
                resolved[key] = value
            else:
                raise TransportError("Environment accepts literal strings or explicit secret references")
        return resolved, secrets


class PinnedSSHClient(asyncssh.SSHClient):
    def __init__(self, expected_host: str, expected_port: int, fingerprint: str):
        self.expected_host = expected_host
        self.expected_port = expected_port
        self.fingerprint = fingerprint.rstrip("=")

    def validate_host_public_key(self, host, addr, port, key):
        return (
            host == self.expected_host
            and port == self.expected_port
            and hmac.compare_digest(key.get_fingerprint("sha256").rstrip("="), self.fingerprint)
        )

    def validate_host_ca_key(self, host, addr, port, key):
        return False


def make_adapter(mode: str, credentials_file=None):
    """Create adapters lazily: demo does not read credentials or contact hosts."""
    from srs.adapters import CompositeAdapter, DemoAdapter

    if mode == "demo":
        return DemoAdapter()
    if mode not in ("local-slurm", "remote-slurm"):
        raise TransportError("Unsupported executor mode")
    return CompositeAdapter(mode, credentials_file)
