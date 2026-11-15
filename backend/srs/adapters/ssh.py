import asyncio
import contextlib
import hashlib
import hmac
import json
import re
import shlex

import asyncssh

from srs.job_logs import sanitize_record, validate_secret_window
from srs.redaction import StreamingRedactor, redact_text, redact_value
from srs.remote_runner import PROTOCOL_VERSION, SUDO_PROMPT, build_manifest, resolve_runtime_argv
from srs.transport import CredentialStore, PinnedSSHClient, TransportError, failure, validate_context


def _redact_nested_json(value, secrets, depth=0):
    """Mask logical values even when a probe embeds JSON as another JSON string."""
    if depth > 40:
        raise TransportError("Structured probe nesting exceeded the safe parsing limit")
    if isinstance(value, dict):
        return {redact_text(key, secrets): _redact_nested_json(item, secrets, depth + 1)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_nested_json(item, secrets, depth + 1) for item in value]
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped[:1] in ("{", "[", '"'):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(decoded, (dict, list, str)):
                    masked = _redact_nested_json(decoded, secrets, depth + 1)
                    # Preserve original bytes and their evidence hash when nothing changed.
                    return json.dumps(masked, ensure_ascii=True) if masked != decoded else value
        return redact_text(value, secrets)
    return value


class SSHAdapter:
    def __init__(self, mode, credentials_file=None, *, connector=None):
        self.mode = mode
        self.credentials = CredentialStore(credentials_file)
        self.connector = connector or asyncssh.connect

    async def run_step(self, step, context, emit):
        secrets = []
        dispatched = False
        remote_result = None
        try:
            host = validate_context(self.mode, step, context)
            if step.get("kind") != "ssh":
                raise TransportError("SSH adapter accepts only approved SSH steps")
            manifest = build_manifest(step, context)
            if step.get("remote_manifest") != manifest:
                raise TransportError("Missing or changed reviewable remote transport manifest")
            argv = step.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and "\0" not in x for x in argv):
                raise TransportError("Approved argv is invalid")
            if not argv[0].startswith("/"):
                raise TransportError("Executable must be absolute")
            if step.get("script") and not (
                len(argv) >= 3 and argv[-2] == "-c" and argv[-1] == step["script"]
            ):
                raise TransportError("Reviewed script must be identical to the explicit argv -c payload")
            for field in ("execution_id", "plan_hash"):
                value = context.get(field, "")
                pattern = r"[a-f0-9]{64}" if field == "plan_hash" else r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}"
                if not isinstance(value, str) or not re.fullmatch(pattern, value):
                    raise TransportError("Invalid executor context: " + field)
            argv = resolve_runtime_argv(step, context)
            structured = manifest["structured_stdout"]
            if structured and step.get("read_only") is not True:
                raise TransportError("Structured output is restricted to approved read-only probes")
            credential = self.credentials.for_host(host, sudo=bool(step.get("requires_sudo")))
            secrets = [credential.password, credential.sudo_password or ""]
            if not step.get("requires_sudo") and manifest["execution_user"] != credential.username:
                raise TransportError("Non-sudo execution identity must equal SSH identity")
            environment, environment_secrets = self.credentials.resolve_environment(step.get("environment") or {})
            secrets.extend(environment_secrets)
            if structured == "job_log":
                validate_secret_window(secrets)
            reviewed = json.dumps({"argv": argv, "cwd": step.get("cwd")}, ensure_ascii=False)
            if any(secret and secret in reviewed for secret in secrets):
                raise TransportError("A credential value is embedded in the approved command; use a secret reference")
            payload = {
                "execution_id": context["execution_id"], "step_id": step.get("id") or context.get("step_id"),
                "plan_hash": context["plan_hash"], "execution_user": manifest["execution_user"],
                "argv": argv, "cwd": step.get("cwd") or "/", "environment": environment,
                "timeout_seconds": step.get("timeout_seconds", 60), "read_only": step.get("read_only") is True,
                "preconditions": step.get("preconditions") or [], "redactions": [s for s in secrets if s],
                "reconcile_only": context.get("reconcile_only") is True,
                "structured_stdout": structured,
            }
            if payload["read_only"] and payload["reconcile_only"]:
                return failure("Read-only output was interrupted; this adapter does not replay it", unknown=True)
            pin = host["host_key_sha256"].rstrip("=")
            async with self.connector(
                host["hostname"], port=host.get("port", 22), username=credential.username,
                password=credential.password, client_keys=[], agent_path=None, config=[],
                known_hosts=[], server_host_key_algs="default", public_key_auth=False,
                kbdint_auth=False, preferred_auth=["password"], connect_timeout=10, login_timeout=10,
                client_factory=lambda: PinnedSSHClient(host["hostname"], host.get("port", 22), pin),
            ) as connection:
                actual_key = connection.get_server_host_key()
                if actual_key is None or not hmac.compare_digest(actual_key.get_fingerprint("sha256").rstrip("="), pin):
                    raise TransportError("SSH host-key fingerprint does not match approved inventory")
                process = await connection.create_process(shlex.join(manifest["launch_argv"]), encoding="utf-8")
                # Wrapper startup is itself reviewed; the target action starts only after payload.
                sudo_sent = False
                ready = False
                stream_redactors = {name: StreamingRedactor(secrets) for name in ("stdout", "stderr", "audit")}

                structured_pending = ""
                artifact_jobs = {item.get("job_id") for item in step.get("evidence", []) if isinstance(item, dict)}

                async def safe_emit(stream, text):
                    nonlocal structured_pending
                    if stream == "stdout" and structured:
                        structured_pending += text
                        if len(structured_pending) > 2_000_000:
                            raise TransportError("Structured probe record exceeded size limit")
                        while "\n" in structured_pending:
                            line, structured_pending = structured_pending.split("\n", 1)
                            if not line:
                                continue
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise TransportError("Malformed structured probe output") from exc
                            if structured == "job_log":
                                safe_record = sanitize_record(record, step["job_log_scope"],
                                                              context["job_log_offsets"], secrets)
                            elif structured == "probe_json":
                                if (not isinstance(record, dict)
                                        or record.get("kind") not in manifest["structured_record_kinds"]):
                                    raise TransportError("Structured probe record is outside the approved operation")
                                safe_record = _redact_nested_json(record, secrets)
                            else:
                                if (not isinstance(record, dict) or record.get("kind") != "artifact"
                                        or record.get("job_id") not in artifact_jobs
                                        or record.get("filename") not in ("rank-0.json", "rank-1.json")
                                        or not isinstance(record.get("text"), str)):
                                    raise TransportError("Artifact record is outside the approved result scope")
                                digest = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()
                                if digest != record.get("source_sha256"):
                                    raise TransportError("Artifact content does not match its source SHA256")
                                try:
                                    artifact_content = json.loads(record["text"])
                                except json.JSONDecodeError as exc:
                                    raise TransportError("Artifact file is not valid UTF-8 JSON") from exc
                                if not isinstance(artifact_content, dict):
                                    raise TransportError("Artifact file must be a JSON object")
                                safe_record = _redact_nested_json(record, secrets)
                                safe_record["source_sha256"] = record["source_sha256"]
                            await emit(stream, json.dumps(safe_record, ensure_ascii=True) + "\n")
                        return
                    safe = stream_redactors[stream].feed(text)
                    if safe:
                        await emit(stream, safe)

                async def stderr_pump():
                    nonlocal sudo_sent
                    pending = ""
                    while True:
                        chunk = await process.stderr.read(4096)
                        if not chunk:
                            break
                        pending += chunk
                        if SUDO_PROMPT in pending:
                            before, pending = pending.split(SUDO_PROMPT, 1)
                            if before:
                                await safe_emit("stderr", before)
                            if not step.get("requires_sudo") or sudo_sent or not credential.sudo_password:
                                raise TransportError("Sudo authentication required or rejected; no fallback credential used")
                            process.stdin.write(credential.sudo_password + "\n")
                            await process.stdin.drain()
                            sudo_sent = True
                        retain = len(SUDO_PROMPT) - 1
                        if len(pending) > retain:
                            await safe_emit("stderr", pending[:-retain])
                            pending = pending[-retain:]
                    if pending:
                        await safe_emit("stderr", pending)

                async def stdout_pump():
                    nonlocal dispatched, ready, remote_result
                    last_sequence = 0
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            if structured_pending:
                                raise TransportError("Structured probe ended with an incomplete record; cursor not advanced")
                            return
                        if len(line) > 2_000_000:
                            raise TransportError("Remote protocol record exceeded size limit")
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise TransportError("Unexpected remote protocol output") from exc
                        if item.get("type") == "ready":
                            if ready or item.get("protocol") != PROTOCOL_VERSION:
                                raise TransportError("Remote wrapper protocol mismatch")
                            ready = True
                            # Set uncertain before handing bytes to the OS; a lost ack must not replay.
                            dispatched = True
                            process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
                            await process.stdin.drain()
                            process.stdin.write_eof()
                        elif item.get("type") == "event":
                            if not ready or item.get("stream") not in stream_redactors:
                                raise TransportError("Invalid remote event")
                            sequence = item.get("sequence")
                            if not isinstance(sequence, int) or sequence <= last_sequence:
                                raise TransportError("Remote event sequence is not increasing")
                            last_sequence = sequence
                            await safe_emit(item["stream"], str(item.get("text", "")))
                        elif item.get("type") == "result":
                            if not ready or remote_result is not None or not isinstance(item.get("result"), dict):
                                raise TransportError("Invalid remote completion record")
                            remote_result = redact_value(item["result"], secrets)
                        else:
                            raise TransportError("Unknown remote protocol record")

                tasks = [asyncio.create_task(stdout_pump()), asyncio.create_task(stderr_pump())]
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks), timeout=step.get("timeout_seconds", 60) + 40)
                    await process.wait_closed()
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    for task in tasks:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                    for stream, redactor in stream_redactors.items():
                        tail = redactor.flush()
                        if tail:
                            await emit(stream, tail)
                if remote_result is None:
                    return failure("Connection ended without a durable completion record; reconcile before any retry",
                                   unknown=dispatched)
                return remote_result
        except asyncio.CancelledError:
            return failure("Executor interrupted; remote completion must be reconciled", unknown=dispatched)
        except Exception as exc:
            message = redact_text(str(exc), secrets)
            await emit("audit", "SSH step stopped: " + message)
            return failure(message, unknown=dispatched, exit_code=None if dispatched else 65)
