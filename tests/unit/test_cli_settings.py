import json
from pathlib import Path

import pytest
from pydantic import HttpUrl

from cache_cli.settings import parse_args

REQUEST = json.dumps({"list_1": ["a"], "list_2": ["b"]})


def parse_error(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)
    assert exit_info.value.code == 2
    return capsys.readouterr().err


def test_defaults() -> None:
    settings = parse_args(["-j", REQUEST])

    assert settings.host == HttpUrl("http://localhost:8000")
    assert settings.repeat == 1
    assert settings.json_body == REQUEST
    assert settings.input_file is None
    assert settings.output_file == "-"


def test_short_flags(tmp_path: Path) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(REQUEST)

    settings = parse_args(
        ["-h", "http://cache:9000", "-r", "3", "-i", str(request_file), "-o", "-"]
    )

    assert settings.host == HttpUrl("http://cache:9000")
    assert settings.repeat == 3
    assert settings.input_file == str(request_file)


def test_long_flags(tmp_path: Path) -> None:
    output_file = tmp_path / "results.jsonl"

    settings = parse_args(
        [
            "--host=http://cache:9000",
            "--repeat=2",
            "--json",
            REQUEST,
            "--output",
            str(output_file),
        ]
    )

    assert settings.host == HttpUrl("http://cache:9000")
    assert settings.repeat == 2
    assert settings.output_file == str(output_file)


def test_stdin_is_a_valid_input() -> None:
    assert parse_args(["-i", "-"]).input_file == "-"


def test_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {"H": "http://a", "HOST": "http://b", "REPEAT": "7"}.items():
        monkeypatch.setenv(name, value)

    settings = parse_args(["-j", REQUEST])

    assert settings.host == HttpUrl("http://localhost:8000")
    assert settings.repeat == 1


def test_help_uses_documented_usage(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--help"])

    assert exit_info.value.code == 0
    usage = capsys.readouterr().out.splitlines()[0]
    assert usage == (
        "usage: cache-cli [--help] [-h URL] [-r N] [-i FILE|-] [-j JSON] [-o FILE|-]"
    )


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        pytest.param(
            ["-j", REQUEST, "-h", "not a url"],
            "argument -h/--host: Input should be a valid URL",
            id="bad host",
        ),
        pytest.param(
            ["-j", REQUEST, "-r", "0"],
            "argument -r/--repeat: Input should be greater than 0",
            id="zero repeats",
        ),
        pytest.param(
            ["-j", REQUEST, "-r", "many"],
            "argument -r/--repeat: Input should be a valid integer",
            id="non-numeric repeats",
        ),
        pytest.param(
            ["-i", "missing.json"],
            "argument -i/--input: no such file: missing.json",
            id="missing input file",
        ),
        pytest.param(
            ["-j", "{not json"],
            "argument -j/--json: Invalid JSON",
            id="malformed json",
        ),
        pytest.param(
            ["-j", '{"list_1": ["a", "b"], "list_2": ["c"]}'],
            "argument -j/--json: list_1 and list_2 must have the same length",
            id="lists of different length",
        ),
        pytest.param(
            ["-j", REQUEST, "-o", "/missing-directory/results.jsonl"],
            "argument -o/--output: no such directory: /missing-directory",
            id="output in missing directory",
        ),
        pytest.param(
            [], "exactly one of -i/--input or -j/--json is required", id="no input"
        ),
        pytest.param(
            ["-i", "-", "-j", REQUEST],
            "exactly one of -i/--input or -j/--json is required",
            id="two inputs",
        ),
        pytest.param(["--bogus"], "unrecognized arguments: --bogus", id="unknown flag"),
    ],
)
def test_invalid_arguments_exit_with_usage_error(
    capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    error = parse_error(capsys, *argv)

    assert error.startswith("usage: cache-cli")
    assert message in error


def test_output_cannot_be_a_directory(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    error = parse_error(capsys, "-j", REQUEST, "-o", str(tmp_path))

    assert f"argument -o/--output: is a directory: {tmp_path}" in error
