"""Deliberately isolated UI fixture execution. No shell, file, or network I/O."""

import hashlib
import json

from srs.transport import failure


class DemoAdapter:
    async def run_step(self, step, context, emit):
        if context.get("mode") != "demo":
            return failure("Demo adapter cannot execute a real-mode plan", exit_code=65)
        await emit("audit", "[DEMO] Simulated execution; no remote operation occurred.")
        outputs = {"simulation": True}
        request = step.get("request") or {}
        if step.get("operation") == "collect_results":
            scope = step["artifact_scope"]
            report = json.dumps({"simulation": True, "source": "demo", "profile": "single", "completed_step": 3,
                                 "note": "Simulated report; no remote training or GPU execution occurred."}, indent=2)
            await emit("stdout", json.dumps({"kind": "artifact", "job_id": scope["job_id"], "filename": "rank-0.json",
                "source_sha256": hashlib.sha256(report.encode()).hexdigest(), "text": report}) + "\n")
        elif step.get("operation") == "job_status":
            outputs["response"] = {"jobs": [{"job_state": ["COMPLETED"], "simulation": True}]}
            await emit("stdout", "[DEMO] " + json.dumps(outputs["response"]) + "\n")
        elif step.get("operation") == "job_logs" and step.get("job_log_scope"):
            scope = step["job_log_scope"]
            for stream in scope["streams"]:
                start = context["job_log_offsets"][scope["job_id"]][stream]
                line = b"[DEMO] Simulated training completed.\n" if stream == "stdout" else b""
                end = len(line)
                await emit("stdout", json.dumps({"kind": "job_log", "job_id": scope["job_id"], "stream": stream,
                    "requested_start": start, "start": start, "end": max(start, end), "inode": "demo-" + stream,
                    "device": "demo", "size": end, "gap": None, "has_more": False, "exists": True,
                    "text": line[start:].decode()}) + "\n")
        elif request.get("method", "").upper() == "POST" and str(request.get("path", "")).endswith("/job/submit"):
            identity = str(context.get("execution_id", "")) + str(context.get("step_id", step.get("id", "")))
            outputs["job_id"] = "demo-" + str(1000 + int(hashlib.sha256(identity.encode()).hexdigest()[:6], 16) % 900000)
            await emit("stdout", "[DEMO] " + json.dumps({"job_id": outputs["job_id"], "state": "PENDING"}))
        elif step.get("read_only"):
            host = context.get("host", {})
            outputs["snapshot"] = {"host_id": host.get("id"), "simulation": True, "source": "demo",
                                   "hardware": host.get("hardware", {}), "available": True}
            await emit("stdout", "[DEMO] " + json.dumps(outputs["snapshot"], ensure_ascii=False))
        else:
            await emit("stdout", "[DEMO] Approved step simulated successfully.\n")
        return {"exit_code": 0, "signal": None, "timed_out": False, "unknown": False, "outputs": outputs}
