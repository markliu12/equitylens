from __future__ import annotations

import io

from equitylens import cli


def test_ensure_utf8_console_handles_legacy_windows_encoding(monkeypatch) -> None:
    output_bytes = io.BytesIO()
    error_bytes = io.BytesIO()
    output = io.TextIOWrapper(output_bytes, encoding="cp1252")
    error = io.TextIOWrapper(error_bytes, encoding="cp1252")
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli.sys, "stderr", error)

    cli.ensure_utf8_console()
    print("状态：完成")
    output.flush()

    assert output.encoding.lower() == "utf-8"
    assert output_bytes.getvalue().decode("utf-8").rstrip("\r\n") == "状态：完成"
