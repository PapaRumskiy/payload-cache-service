"""HTTP endpoints."""

import json
import math
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from cache_service.models import Payload
from cache_service.schemas import PayloadCreate, PayloadCreated, PayloadRead
from cache_service.service import PayloadService
from cache_service.transformer import TransformerError

router = APIRouter()


async def get_payload_service(request: Request) -> PayloadService:
    service: PayloadService = request.app.state.payload_service
    return service


PayloadServiceDep = Annotated[PayloadService, Depends(get_payload_service)]


@router.post(
    "/payload",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": PayloadCreated,
            "description": "The same payload was generated before; its id is reused.",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "The transformer service failed; retrying is safe.",
        },
    },
)
async def create_payload(
    body: PayloadCreate,
    request: Request,
    response: Response,
    service: PayloadServiceDep,
) -> PayloadCreated:
    """Generate a payload from two lists and return its id."""
    try:
        payload_id, created = await service.create_payload(body.list_1, body.list_2)
    except TransformerError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Transformer service failed"
        ) from exc
    response.headers["Location"] = str(
        request.url_for("read_payload", payload_id=str(payload_id))
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return PayloadCreated(
        id=payload_id,
        message="Payload created" if created else "Payload already exists",
    )


@router.get(
    "/payload/{payload_id}",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No payload has this id."}},
)
async def read_payload(
    payload_id: uuid.UUID, service: PayloadServiceDep
) -> PayloadRead:
    """Return a generated payload."""
    output = await service.get_output(payload_id)
    if output is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payload not found")
    return PayloadRead(output=output)


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    """Report whether the database is reachable and migrated."""
    try:
        async with request.app.state.sessions() as session:
            await session.execute(select(Payload.id).limit(1))
    # asyncpg raises plain OSErrors, e.g. when the connection is refused.
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from exc
    return {"status": "ok"}


class StrictJSONResponse(JSONResponse):
    """JSON escaped to ASCII, without the non-standard NaN and Infinity."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, separators=(",", ":"), allow_nan=False).encode()


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> StrictJSONResponse:
    """FastAPI's 422 response, made safe for whatever the client sent.

    The errors echo the rejected input, which can be bytes that are not UTF-8,
    NaN, or a lone surrogate (valid as a JSON escape, invalid as text). The
    default handler fails with a 500 on all three; here bytes are decoded
    leniently, non-finite numbers become strings, and the JSON is ASCII.
    """
    errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={
            bytes: lambda raw: raw.decode("utf-8", "backslashreplace"),
            float: lambda number: number if math.isfinite(number) else str(number),
        },
    )
    return StrictJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors}
    )
