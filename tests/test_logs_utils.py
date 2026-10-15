from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils import logs as logs_utils


@pytest.fixture
def log_dir(tmp_path):
    with patch.object(logs_utils.config, "LOG_DIR", tmp_path):
        yield tmp_path


def _write_log(path: Path, n: int) -> None:
    path.write_text("\n".join(f"2026-10-15 10:00:{i:02d} [INFO] videolib: line {i}" for i in range(n)) + "\n")


def test_tail_lines_returns_last_n(log_dir):
    f = log_dir / "videolib.log"
    _write_log(f, 100)
    lines = logs_utils.tail_lines(f, 5)
    assert len(lines) == 5
    assert lines[-1].endswith("line 99")
    assert lines[0].endswith("line 95")


def test_tail_lines_missing_file(log_dir):
    assert logs_utils.tail_lines(log_dir / "nope.log", 5) == []


def test_grep_errors_filters_levels(log_dir):
    f = log_dir / "videolib.log"
    f.write_text(
        "2026-10-15 [INFO] videolib: fine\n"
        "2026-10-15 [WARNING] videolib: careful\n"
        "2026-10-15 [ERROR] videolib: bad\n"
        "2026-10-15 [DEBUG] videolib: noise\n"
    )
    assert logs_utils.grep_errors(f, 10) == [
        "2026-10-15 [WARNING] videolib: careful",
        "2026-10-15 [ERROR] videolib: bad",
    ]
    assert logs_utils.grep_errors(f, 1) == ["2026-10-15 [ERROR] videolib: bad"]


def test_failure_reports_listing_and_resolution(log_dir):
    failures = log_dir / "failures"
    failures.mkdir()
    old = failures / "failed_20260101_000000_aaaa.log"
    new = failures / "failed_20260102_000000_bbbb.log"
    old.write_text("old")
    new.write_text("new report")
    (failures / "notes.txt").write_text("ignored")

    reports = logs_utils.list_failure_reports(limit=10)
    assert [r.name for r in reports] == [new.name, old.name]
    assert logs_utils.list_failure_reports(limit=1) == [new]

    assert logs_utils.resolve_failure_report("latest") == new
    assert logs_utils.resolve_failure_report(old.name) == old
    assert logs_utils.resolve_failure_report("../videolib.log") is None
    assert logs_utils.resolve_failure_report("failed_missing.log") is None
    assert logs_utils.resolve_failure_report("notes.txt") is None


def test_render_pre_escapes_and_truncates():
    out = logs_utils.render_pre("a <b> & c")
    assert out == "<pre>a &lt;b&gt; &amp; c</pre>"
    long_text = "\n".join(f"line {i}" for i in range(1000))
    out = logs_utils.render_pre(long_text, max_chars=200)
    assert len(out) <= 260
    assert out.startswith("<pre>… ")
    assert "lines omitted" in out
    assert out.endswith("line 999</pre>")


def test_app_log_path(log_dir):
    assert logs_utils.app_log_path() == log_dir / "videolib.log"
