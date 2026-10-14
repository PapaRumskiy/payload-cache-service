import asyncio
import io
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine

from cache_cli.main import main
from cache_service.config import Settings
from cache_service.main import create_app
from cache_service.models import Base
from tests.fakes import CountingTransformer

REQUEST = {
    "list_1": ["first string", "second string", "third string"],
    "list_2": ["other string", "another string", "last string"],
}
OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, "
    "ANOTHER STRING, THIRD STRING, LAST STRING"
)


async def reset_schema(database_url: str, *, create: bool) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        if create:
            await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer()


@pytest.fixture
def server_url(database_url: str, transformer: CountingTransformer) -> Iterator[str]:
    """Run the service with uvicorn in a background thread.

    The CLI talks plain HTTP, so it is tested against a real server.
    """
    asyncio.run(reset_schema(database_url, create=True))
    app = create_app(
        Settings(_env_file=None, database_url=database_url), transformer=transformer
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive(), "server failed to start"
        assert time.monotonic() < deadline, "server did not start in time"
        time.sleep(0.01)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join()
    listener.close()
    asyncio.run(reset_schema(database_url, create=False))


def output_lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines()]


def test_repeated_runs_reuse_the_payload(
    server_url: str,
    transformer: CountingTransformer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = main(["-h", server_url, "-r", "3", "-j", json.dumps(REQUEST)])

    captured = capsys.readouterr()
    assert status == 0
    lines = output_lines(captured.out)
    assert [line["iteration"] for line in lines] == [1, 2, 3]
    assert [line["created"] for line in lines] == [True, False, False]
    assert len({line["id"] for line in lines}) == 1
    assert all(line["output"] == OUTPUT for line in lines)
    assert "3 iteration(s): 1 created, 2 reused" in captured.err
    assert transformer.total_calls == 6


def test_reads_the_request_from_a_file_and_writes_to_a_file(
    server_url: str, tmp_path: Path
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(REQUEST))
    results_file = tmp_path / "results.jsonl"

    status = main(
        ["--host", server_url, "--input", str(request_file), "-o", str(results_file)]
    )

    assert status == 0
    [line] = output_lines(results_file.read_text())
    assert line["output"] == OUTPUT


def test_reads_the_request_from_stdin(
    server_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(REQUEST).encode()))
    monkeypatch.setattr("sys.stdin", stdin)

    status = main(["-h", server_url, "-i", "-"])

    assert status == 0
    [line] = output_lines(capsys.readouterr().out)
    assert line["output"] == OUTPUT


def test_invalid_request_file_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text('{"list_1": ["a"]}')

    status = main(["-i", str(request_file)])

    assert status == 2
    assert "invalid request: list_2: Field required" in capsys.readouterr().err


def test_request_file_that_is_not_utf8_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_bytes(b"\xff\xfe not json")

    status = main(["-i", str(request_file)])

    assert status == 2
    assert "invalid request: Invalid JSON" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_unwritable_output_is_a_usage_error(
    server_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o500)

    status = main(
        ["-h", server_url, "-j", json.dumps(REQUEST), "-o", str(read_only / "out")]
    )

    assert status == 2
    assert "cannot write the results" in capsys.readouterr().err


def test_reader_closing_the_pipe_early_is_not_an_error(server_url: str) -> None:
    # Enough output to overflow the pipe buffer once the reader is gone.
    request = {"list_1": ["x" * 500] * 10, "list_2": ["y" * 500] * 10}
    command = [sys.executable, "-m", "cache_cli", "-h", server_url, "-r", "50"]
    cli = subprocess.Popen(  # noqa: S603 - our own interpreter and arguments
        [*command, "-j", json.dumps(request)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert cli.stdout is not None
    assert cli.stderr is not None

    first_line = cli.stdout.readline()
    cli.stdout.close()
    stderr = cli.stderr.read().decode()
    cli.stderr.close()

    assert cli.wait(timeout=30) == 0
    assert json.loads(first_line)["iteration"] == 1
    assert stderr == ""


def test_service_failure_is_reported(
    server_url: str,
    transformer: CountingTransformer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transformer.fail_on.add("a")

    status = main(["-h", server_url, "-j", '{"list_1": ["a"], "list_2": ["b"]}'])

    assert status == 1
    assert "POST /payload failed with status 502" in capsys.readouterr().err


def test_unreachable_service_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
    # Nothing listens on the port any more.

    status = main(["-h", f"http://127.0.0.1:{port}", "-j", json.dumps(REQUEST)])

    assert status == 1
    assert "POST /payload failed" in capsys.readouterr().err
