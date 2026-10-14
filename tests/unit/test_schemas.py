import pytest
from pydantic import ValidationError

from cache_service.schemas import MAX_ITEMS, MAX_STRING_LENGTH, PayloadCreate


def test_accepts_lists_of_equal_length() -> None:
    body = PayloadCreate.model_validate({"list_1": ["a", "b"], "list_2": ["c", "d"]})

    assert body.list_1 == ["a", "b"]
    assert body.list_2 == ["c", "d"]


def test_rejects_lists_of_different_length() -> None:
    with pytest.raises(ValidationError, match="must have the same length"):
        PayloadCreate.model_validate({"list_1": ["a", "b"], "list_2": ["c"]})


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"list_1": ["a"]}, id="missing list"),
        pytest.param({"list_1": [], "list_2": []}, id="empty lists"),
        pytest.param({"list_1": [1], "list_2": ["a"]}, id="non-string item"),
        pytest.param({"list_1": "a", "list_2": "b"}, id="string instead of list"),
        pytest.param(
            {"list_1": ["a"], "list_2": ["b"], "list_3": ["c"]}, id="unknown field"
        ),
        pytest.param(
            {"list_1": ["a"] * (MAX_ITEMS + 1), "list_2": ["b"] * (MAX_ITEMS + 1)},
            id="too many items",
        ),
        pytest.param(
            {"list_1": ["a" * (MAX_STRING_LENGTH + 1)], "list_2": ["b"]},
            id="string too long",
        ),
    ],
)
def test_rejects_malformed_bodies(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PayloadCreate.model_validate(body)
