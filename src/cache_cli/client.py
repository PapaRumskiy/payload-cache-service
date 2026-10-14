"""HTTP client for the caching service."""

import uuid
from typing import Any, NamedTuple

import httpx

from cache_service.schemas import PayloadCreate, PayloadCreated, PayloadRead


class ServiceError(Exception):
    """The service could not be reached or answered with an error."""


class CreateResult(NamedTuple):
    payload_id: uuid.UUID
    created: bool


class CacheServiceClient:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def create_payload(self, request: PayloadCreate) -> CreateResult:
        response = self._send("POST", "/payload", json=request.model_dump())
        body = PayloadCreated.model_validate_json(response.content)
        return CreateResult(body.id, response.status_code == httpx.codes.CREATED)

    def read_payload(self, payload_id: uuid.UUID) -> str:
        response = self._send("GET", f"/payload/{payload_id}")
        return PayloadRead.model_validate_json(response.content).output

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ServiceError(
                f"{method} {url} failed with status {error.response.status_code}: "
                f"{error.response.text}"
            ) from error
        except httpx.HTTPError as error:
            raise ServiceError(f"{method} {url} failed: {error}") from error
        return response
