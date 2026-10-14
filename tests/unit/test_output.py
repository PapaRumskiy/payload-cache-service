import pytest

from cache_service.service import interleave, render_output


def test_interleave_alternates_items() -> None:
    assert interleave([1, 3, 5], [2, 4, 6]) == [1, 2, 3, 4, 5, 6]


def test_interleave_of_empty_sequences_is_empty() -> None:
    assert interleave([], []) == []


def test_interleave_rejects_sequences_of_different_length() -> None:
    with pytest.raises(ValueError, match="zip"):
        interleave([1, 2], [3])


def test_render_output_matches_spec_example() -> None:
    output = render_output(
        ["FIRST STRING", "SECOND STRING", "THIRD STRING"],
        ["OTHER STRING", "ANOTHER STRING", "LAST STRING"],
    )

    assert output == (
        "FIRST STRING, OTHER STRING, SECOND STRING, "
        "ANOTHER STRING, THIRD STRING, LAST STRING"
    )
