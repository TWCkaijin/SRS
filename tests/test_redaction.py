import pytest
from srs.redaction import StreamingRedactor, redact_text, redact_value


@pytest.mark.parametrize("cut", range(1, 24))
def test_secret_split_at_any_boundary(cut):
    text = "prefix password-123456 suffix"
    redactor = StreamingRedactor(["password-123456"])
    pieces = [redactor.feed(text[:cut]), redactor.feed(text[cut:]), redactor.flush()]
    assert "".join(pieces) == "prefix [REDACTED] suffix"
    assert all("password-123456" not in piece for piece in pieces)


def test_character_by_character_unicode_and_overlapping_secrets():
    redactor = StreamingRedactor(["密碼abc", "密碼", "abc", "abcXYZ"])
    output = "".join(redactor.feed(character) for character in "a密碼abc b abcXYZ c")
    assert output + redactor.flush() == "a[REDACTED] b [REDACTED] c"


def test_flush_preserves_nonsecret_suffix_and_clears_state():
    redactor = StreamingRedactor(["very-long-secret"])
    assert redactor.feed("partial") == ""
    assert redactor.flush() == "partial"
    assert redactor.flush() == ""


def test_empty_secrets_never_mask_everything():
    redactor = StreamingRedactor(["", None])
    assert redactor.feed("hello") + redactor.flush() == "hello"


def test_structured_redaction_preserves_numeric_values():
    assert redact_value({"job_id": 123, "output": ["token=secret-value"]}, ["secret-value"]) == {
        "job_id": 123, "output": ["token=[REDACTED]"]
    }
    assert redact_text("secretssecret", ["secret"]) == "[REDACTED]s[REDACTED]"


def test_nontext_input_rejected():
    with pytest.raises(TypeError):
        StreamingRedactor().feed(b"not decoded")
