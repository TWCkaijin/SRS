"""Bounded Slurm REST operations, with write-ahead dispatch records.

No retry can turn an ambiguous POST into a second training job. Reconciliation
only issues an additional GET when that GET was included in the approved plan.
"""

import base64
import hashlib
import ipaddress
import json
import os
import re
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from srs.redaction import redact_text, redact_value
from srs.transport import CredentialStore, TransportError, failure, validate_context

API_PREFIX = "/slurm/v0.0.45"


def _validate_request(request, context):
    method = str(request.get("method", "")).upper()
    path = request.get("path", "")
    allowed = (
        method == "GET" and re.fullmatch(re.escape(API_PREFIX) + r"/(?:jobs|nodes|job/[0-9]+|jobs/state)/?", path)
        or method == "POST" and path == API_PREFIX + "/job/submit"
        or method == "DELETE" and re.fullmatch(re.escape(API_PREFIX) + r"/job/[0-9]+", path)
    )
    if not allowed:
        raise TransportError("Slurm operation is not in the fixed v0.0.45 API allowlist")
    if method != "POST" and request.get("body") not in (None, {}):
        raise TransportError("This Slurm operation does not accept a request body")
    base = request.get("base_url", "")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TransportError("Invalid Slurm API base URL")
    if parsed.path not in ("", "/") or not parsed.hostname:
        raise TransportError("Slurm base URL must not include an API path")
    host = context["host"]
    configured = context.get("slurm_api_base_url") or host.get("slurm_api_base_url")
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    if configured:
        if base.rstrip("/") != configured.rstrip("/"):
            raise TransportError("Slurm endpoint differs from trusted executor configuration")
    elif not loopback or parsed.port != 6820:
        raise TransportError("A non-loopback Slurm endpoint requires explicit trusted configuration")
    if parsed.scheme == "http" and not loopback:
        try:
            private = ipaddress.ip_address(parsed.hostname).is_private
        except ValueError:
            private = False
        if not private:
            raise TransportError("Unencrypted Slurm API is limited to loopback or configured private addresses")
    return method, base.rstrip("/") + path


def _token_identity(token, username):
    try:
        parts = token.split(".")
        if len(parts) != 3 or len(token) > 16384:
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        if payload.get("sun") != username or float(payload.get("exp", 0)) <= time.time():
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise TransportError("Slurm token must be unexpired and scoped to the approved execution user") from exc
    # This is a scope sanity check, not signature verification. Slurm verifies it.


class SlurmAdapter:
    def __init__(self, mode, credentials_file=None, *, client_factory=None):
        self.mode = mode
        self.credentials = CredentialStore(credentials_file)
        self.client_factory = client_factory or httpx.AsyncClient

    def _headers(self, step):
        values = self.credentials.read()
        username = values.get("SLURM_USERNAME", "")
        token = values.get("SLURM_TOKEN", "")
        if not username or username in ("root", "slurm") or username != step.get("execution_user"):
            raise TransportError("Slurm credential identity must equal the approved non-privileged execution user")
        if not token:
            raise TransportError("Executor-only SLURM_TOKEN is not configured")
        _token_identity(token, username)
        expected = {
            "X-SLURM-USER-NAME": {"secret_ref": "SLURM_USERNAME"},
            "X-SLURM-USER-TOKEN": {"secret_ref": "SLURM_TOKEN"},
        }
        headers = (step.get("request") or {}).get("headers")
        if headers not in (None, expected):
            raise TransportError("Slurm headers must use the fixed executor credential references")
        return {"X-SLURM-USER-NAME": username, "X-SLURM-USER-TOKEN": token}, [token]

    @staticmethod
    def _ledger(context, step):
        execution_id = context.get("execution_id", "")
        step_id = step.get("id") or context.get("step_id", "")
        for value in (execution_id, step_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", value):
                raise TransportError("Invalid local dispatch identifier")
        data_dir = context.get("data_dir")
        if not data_dir:
            raise TransportError("Durable executor data_dir is required")
        root = Path(data_dir) / "adapter-journal"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise TransportError("Adapter dispatch journal must be private and owned by the executor")
        return root / (execution_id + "--" + step_id + ".json")

    @staticmethod
    def _write_ledger(path, value, *, reserve=False):
        if reserve:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            return
        temp = path.with_suffix(".tmp-" + str(os.getpid()))
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)

    async def run_step(self, step, context, emit):
        secrets = []
        dispatched = False
        mutation = False
        ledger = None
        record = None
        try:
            validate_context(self.mode, step, context)
            if step.get("kind") != "slurm_api" or step.get("requires_sudo"):
                raise TransportError("Slurm REST actions cannot request sudo")
            if not re.fullmatch(r"[a-f0-9]{64}", context.get("plan_hash", "")):
                raise TransportError("Approved plan hash is required")
            original = step.get("request") or {}
            request = dict(original)
            original_method, _ = _validate_request(original, context)
            reconcile = context.get("reconcile_only") is True
            if reconcile:
                approved = step.get("reconcile_request")
                if not isinstance(approved, dict) or approved.get("method") != "GET":
                    return failure("No approved reconciliation GET exists; manual confirmation required", unknown=True)
                if original_method != "POST" or original.get("path") != API_PREFIX + "/job/submit":
                    return failure("This operation has no implemented reconciliation proof", unknown=True)
                comment = ((original.get("body") or {}).get("job") or {}).get("comment")
                if not comment or comment != approved.get("match_comment"):
                    raise TransportError("Reconciliation must match the approved submission comment")
                request = {**approved, "base_url": original.get("base_url")}
            method, url = _validate_request(request, context)
            mutation = method in ("POST", "DELETE")
            if step.get("read_only") and mutation:
                raise TransportError("A read-only plan cannot dispatch a Slurm mutation")
            headers, secrets = self._headers(step)
            if any(secret in json.dumps(original, ensure_ascii=False) for secret in secrets):
                raise TransportError("A Slurm credential is embedded in the approved request")
            if mutation:
                ledger = self._ledger(context, step)
                fingerprint = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                record = {"plan_hash": context["plan_hash"], "request_hash": fingerprint, "status": "dispatching"}
                try:
                    self._write_ledger(ledger, record, reserve=True)
                except FileExistsError:
                    try:
                        saved = json.loads(ledger.read_text())
                    except (OSError, json.JSONDecodeError):
                        return failure("Dispatch journal is incomplete; reconcile instead of retrying", unknown=True)
                    if saved.get("plan_hash") != record["plan_hash"] or saved.get("request_hash") != fingerprint:
                        raise TransportError("Dispatch identifier is already bound to another approved request") from None
                    if saved.get("status") == "finished":
                        await emit("audit", "Returning durable completion; no Slurm request was resent.")
                        return saved["result"]
                    return failure("A previous dispatch may have reached Slurm; reconciliation required", unknown=True)
            await emit("audit", "Dispatching approved Slurm " + method + " " + request["path"])
            async with self.client_factory(timeout=min(step.get("timeout_seconds", 20), 20), follow_redirects=False, trust_env=False) as client:
                dispatched = True
                response = await client.request(method, url, headers=headers, json=request.get("body"))
            if len(response.content) > 2_000_000:
                return failure("Slurm response exceeded the permitted size", unknown=mutation)
            text = redact_text(response.text, secrets)
            await emit("stdout" if response.is_success else "stderr", text)
            if not response.is_success:
                result = failure("Slurm returned HTTP " + str(response.status_code),
                                 unknown=mutation and response.status_code >= 500, exit_code=1)
            else:
                try:
                    body = response.json() if response.content else {}
                except ValueError:
                    return failure("Slurm returned an invalid JSON response", unknown=mutation)
                if not isinstance(body, dict):
                    return failure("Slurm returned an unexpected response shape", unknown=mutation)
                safe_body = redact_value(body, secrets)
                result = {"exit_code": 0, "signal": None, "timed_out": False, "unknown": False,
                          "outputs": {"response": safe_body}}
                if reconcile:
                    matches = [job for job in body.get("jobs", []) if isinstance(job, dict)
                               and job.get("comment") == request["match_comment"]
                               and job.get("user_name", job.get("user", step["execution_user"])) == step["execution_user"]]
                    if len(matches) != 1 or not str(matches[0].get("job_id", "")).isdigit():
                        return failure("Reconciliation did not identify exactly one owned job; do not resubmit", unknown=True)
                    result["outputs"]["job_id"] = str(matches[0]["job_id"])
                    result["outputs"]["reconciled"] = True
                elif method == "POST":
                    job_id = body.get("job_id")
                    if body.get("errors") or not str(job_id).isdigit() or int(job_id) <= 0:
                        return failure("Submission response has no unambiguous successful job ID; reconcile", unknown=True)
                    result["outputs"]["job_id"] = str(job_id)
                elif body.get("errors"):
                    result = failure("Slurm reported operation errors", exit_code=1)
            if reconcile and not result.get("unknown"):
                ledger = self._ledger(context, step)
                fingerprint = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                record = {"plan_hash": context["plan_hash"], "request_hash": fingerprint}
                if ledger.exists():
                    saved = json.loads(ledger.read_text())
                    if saved.get("plan_hash") != record["plan_hash"] or saved.get("request_hash") != fingerprint:
                        raise TransportError("Reconciliation journal belongs to another approved request")
            if ledger is not None and record is not None and not result.get("unknown"):
                self._write_ledger(ledger, {**record, "status": "finished", "result": result})
            return result
        except Exception as exc:
            message = redact_text(str(exc), secrets)
            await emit("audit", "Slurm step stopped: " + message)
            return failure(message, unknown=dispatched and mutation, exit_code=None if dispatched and mutation else 65)
