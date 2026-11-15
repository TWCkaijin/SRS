"""Streaming masking for *known* secrets, before logs leave the executor.

This cannot identify unknown credentials, arbitrary encodings, or secrets a
program deliberately transforms. It preserves text, not original byte streams.
"""


class StreamingRedactor:
    """Delay a short suffix so a credential split across reads cannot escape."""

    def __init__(self, secrets=(), replacement="[REDACTED]"):
        self.secrets = sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True)
        self.replacement = replacement
        self.pending = ""
        self.max_length = max((len(s) for s in self.secrets), default=1)

    def _consume(self, final):
        limit = len(self.pending) if final else max(0, len(self.pending) - self.max_length + 1)
        parts = []
        index = 0
        while index < limit:
            match = next((s for s in self.secrets if self.pending.startswith(s, index)), None)
            if match:
                parts.append(self.replacement)
                index += len(match)
            else:
                parts.append(self.pending[index])
                index += 1
        self.pending = self.pending[index:]
        return "".join(parts)

    def feed(self, text):
        if not isinstance(text, str):
            raise TypeError("StreamingRedactor accepts decoded text")
        self.pending += text
        return self._consume(False)

    def flush(self):
        return self._consume(True)


def redact_text(text: str, secrets=()) -> str:
    redactor = StreamingRedactor(secrets)
    return redactor.feed(text) + redactor.flush()


def redact_value(value, secrets=()):
    """Mask string values in structured results without breaking JSON numbers."""
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, secrets) for key, item in value.items()}
    return value
