"""Request and response bodies of the payload API."""

import uuid
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# Every distinct string may cost one call to the transformer service and one
# bound parameter in the cache lookup, so the request size is capped to keep
# the worst case per request predictable.
MAX_ITEMS = 1_000
MAX_STRING_LENGTH = 1_000


def _without_nul(value: str) -> str:
    # Postgres text columns cannot store NUL characters.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


InputString = Annotated[
    str, StringConstraints(max_length=MAX_STRING_LENGTH), AfterValidator(_without_nul)
]
InputList = Annotated[list[InputString], Field(min_length=1, max_length=MAX_ITEMS)]


class PayloadCreate(BaseModel):
    """Two lists whose transformed items are interleaved into one payload."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "list_1": ["first string", "second string", "third string"],
                    "list_2": ["other string", "another string", "last string"],
                }
            ]
        },
    )

    list_1: InputList
    list_2: InputList

    @model_validator(mode="after")
    def _check_same_length(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    """Answer to a create request: where to find the payload."""

    id: uuid.UUID
    message: str


class PayloadRead(BaseModel):
    """A generated payload."""

    output: str
