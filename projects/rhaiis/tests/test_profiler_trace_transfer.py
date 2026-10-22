import gzip
import hashlib
from types import SimpleNamespace

import pytest

from projects.rhaiis.toolbox.copy_profiler_traces import main as transfer


def _mock_remote(monkeypatch, payload: bytes) -> None:
    def remote_text(_pod, _namespace, *command, **kwargs):
        if command[0] == "stat":
            return str(len(payload))
        if command[0] == "sha256sum":
            return hashlib.sha256(payload).hexdigest() + "  /tmp/trace.json"
        raise AssertionError(command)

    monkeypatch.setattr(transfer, "_remote_text", remote_text)


def test_copy_preserves_and_verifies_plain_rank0_trace(monkeypatch, tmp_path) -> None:
    payload = b'{"traceEvents": [1, 2, 3]}' * 100
    _mock_remote(monkeypatch, payload)

    def copy(command, *, stdout, stderr, timeout, check):
        assert stderr is not None and timeout == 30 and check is False
        assert command[-2:] == ["cat", f"/tmp/{filename}"]
        stdout.write(payload)
        return SimpleNamespace(returncode=0, stderr=b"")

    filename = "trace_rank0_pid100_runprofile1_range500-503.json"
    monkeypatch.setattr(transfer.subprocess, "run", copy)

    copied = transfer._copy_one_trace("pod/model", "test", filename, tmp_path, 30)

    assert copied.name == filename
    assert copied.read_bytes() == payload
    assert not list(tmp_path.glob("*.partial"))


def test_truncated_transfer_is_removed_before_retry(monkeypatch, tmp_path) -> None:
    payload = b'{"traceEvents": [1, 2, 3]}' * 100
    _mock_remote(monkeypatch, payload)
    attempts = 0

    def copy(_command, *, stdout, stderr, timeout, check):
        nonlocal attempts
        attempts += 1
        stdout.write(payload[:-8] if attempts == 1 else payload)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(transfer.subprocess, "run", copy)
    filename = "trace_rank0_pid100_runprofile4_range500-503.json"

    with pytest.raises(RuntimeError, match="produced"):
        transfer._copy_one_trace("pod/model", "test", filename, tmp_path, 30)
    assert not list(tmp_path.iterdir())

    copied = transfer._copy_one_trace("pod/model", "test", filename, tmp_path, 30)
    assert attempts == 2
    assert copied.read_bytes() == payload


def test_existing_gzip_trace_is_decoded_to_plain_json(monkeypatch, tmp_path) -> None:
    payload = b'{"traceEvents": [1, 2, 3]}'
    compressed = gzip.compress(payload)
    _mock_remote(monkeypatch, compressed)

    def copy(_command, *, stdout, stderr, timeout, check):
        stdout.write(compressed)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(transfer.subprocess, "run", copy)
    filename = "trace_rank0_pid100_runprofile1_range500-503.json.gz"

    copied = transfer._copy_one_trace("pod/model", "test", filename, tmp_path, 30)
    assert copied.name == filename.removesuffix(".gz")
    assert copied.read_bytes() == payload
    assert not list(tmp_path.glob("*.partial"))


def test_invalid_gzip_trace_does_not_publish_json(monkeypatch, tmp_path) -> None:
    invalid = b"not a gzip trace"
    _mock_remote(monkeypatch, invalid)

    def copy(_command, *, stdout, stderr, timeout, check):
        stdout.write(invalid)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(transfer.subprocess, "run", copy)

    with pytest.raises(gzip.BadGzipFile):
        transfer._copy_one_trace(
            "pod/model",
            "test",
            "trace_rank0_pid100_runprofile1_range500-503.json.gz",
            tmp_path,
            30,
        )
    assert not list(tmp_path.iterdir())


def test_checksum_mismatch_does_not_publish_trace(monkeypatch, tmp_path) -> None:
    payload = b'{"traceEvents": [1, 2, 3]}'
    _mock_remote(monkeypatch, b"X" + payload[1:])

    def copy(_command, *, stdout, stderr, timeout, check):
        stdout.write(payload)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(transfer.subprocess, "run", copy)

    with pytest.raises(RuntimeError, match="SHA-256 verification"):
        transfer._copy_one_trace(
            "pod/model",
            "test",
            "trace_rank0_pid100_runprofile1_range500-503.json",
            tmp_path,
            30,
        )
    assert not list(tmp_path.iterdir())


def test_copy_task_retries_transfer_failures() -> None:
    assert transfer.copy_traces._retry_config["attempts"] == 3
    assert transfer.copy_traces._retry_config["retry_on_exceptions"] is True
