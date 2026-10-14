import uuid
from collections.abc import Callable

import httpx
import pytest

from cache_cli.client import CacheServiceClient, CreateResult, ServiceError
from cache_service.schemas import PayloadCreate

REQUEST = PayloadCreate(list_1=["a"], list_2=["b"])
PAYLOAD_ID = uuid.uuid4()


def client_for(
    handler: Callable[[httpx.Request], httpx.Response],
) -> CacheServiceClient:
    return CacheServiceClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    )


@pytest.mark.parametrize(("status", "created"), [(201, True), (200, False)])
def test_create_reports_whether_the_payload_is_new(status: int, created: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/payload"
        assert request.read() == REQUEST.model_dump_json().encode()
        return httpx.Response(status, json={"id": str(PAYLOAD_ID), "message": "-"})

    assert client_for(handler).create_payload(REQUEST) == CreateResult(
        PAYLOAD_ID, created
    )


def test_read_returns_the_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/payload/{PAYLOAD_ID}"
        return httpx.Response(200, json={"output": "A, B"})

    assert client_for(handler).read_payload(PAYLOAD_ID) == "A, B"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        pytest.param(
            httpx.Response(502, json={"detail": "Transformer service failed"}),
            "POST /payload failed with status 502",
            id="error status",
        ),
        pytest.param(
            httpx.Response(200, text="<html>not the service</html>"),
            "POST /payload returned an unexpected body",
            id="unexpected body",
        ),
    ],
)
def test_bad_responses_raise_service_error(
    response: httpx.Response, message: str
) -> None:
    with pytest.raises(ServiceError, match=message):
        client_for(lambda _: response).create_payload(REQUEST)


def test_connection_failure_raises_service_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ServiceError, match="POST /payload failed: connection refused"):
        client_for(handler).create_payload(REQUEST)
