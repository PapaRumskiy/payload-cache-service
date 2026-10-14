"""HTTP endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from cache_service.repositories import PayloadRepository, TransformationCache
from cache_service.schemas import PayloadCreate, PayloadCreated, PayloadRead
from cache_service.service import PayloadService

router = APIRouter()


async def get_payload_service(request: Request) -> PayloadService:
    sessions = request.app.state.sessions
    return PayloadService(
        TransformationCache(sessions),
        PayloadRepository(sessions),
        request.app.state.transformer,
    )


PayloadServiceDep = Annotated[PayloadService, Depends(get_payload_service)]


@router.post(
    "/payload",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": PayloadCreated,
            "description": "The same payload was generated before; its id is reused.",
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
    payload_id, created = await service.create_payload(body.list_1, body.list_2)
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
    """Report whether the service can reach its database."""
    try:
        async with request.app.state.sessions() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from exc
    return {"status": "ok"}
