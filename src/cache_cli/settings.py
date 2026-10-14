"""Command line parsing and validation, built on Pydantic Settings."""

from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    AliasChoices,
    Field,
    HttpUrl,
    PositiveInt,
    ValidationError,
    model_validator,
)
from pydantic_core import ErrorDetails
from pydantic_settings import (
    BaseSettings,
    CliApp,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from cache_service.schemas import PayloadCreate

PROG = "cache-cli"
STDIO = "-"

# Shown in --help in place of type names, matching the documented usage line.
_METAVARS = {
    "--host": "URL",
    "--repeat": "N",
    "--input": "FILE|-",
    "--json": "JSON",
    "--output": "FILE|-",
}


def _existing_file_or_stdin(value: str) -> str:
    if value != STDIO and not Path(value).is_file():
        raise ValueError(f"no such file: {value}")
    return value


def _writable_path_or_stdout(value: str) -> str:
    if value == STDIO:
        return value
    path = Path(value)
    if path.is_dir():
        raise ValueError(f"is a directory: {value}")
    if not path.parent.is_dir():
        raise ValueError(f"no such directory: {path.parent}")
    return value


def _request_json(value: str) -> str:
    # Validating here makes a malformed --json fail like any other bad
    # argument, before anything is sent.
    PayloadCreate.model_validate_json(value)
    return value


class CliSettings(BaseSettings):
    model_config = SettingsConfigDict(cli_hide_none_type=True)

    host: HttpUrl = Field(
        default=HttpUrl("http://localhost:8000"),
        validation_alias=AliasChoices("h", "host"),
        description="base URL of the caching service",
    )
    repeat: PositiveInt = Field(
        default=1,
        validation_alias=AliasChoices("r", "repeat"),
        description="number of iterations",
    )
    input_file: Annotated[str, AfterValidator(_existing_file_or_stdin)] | None = Field(
        default=None,
        validation_alias=AliasChoices("i", "input"),
        description="file with the request body ('-' for stdin)",
    )
    json_body: Annotated[str, AfterValidator(_request_json)] | None = Field(
        default=None,
        validation_alias=AliasChoices("j", "json"),
        description="request body as a JSON string",
    )
    output_file: Annotated[str, AfterValidator(_writable_path_or_stdout)] = Field(
        default=STDIO,
        validation_alias=AliasChoices("o", "output"),
        description="file to write the results to ('-' for stdout)",
    )

    @model_validator(mode="after")
    def _exactly_one_request_source(self) -> Self:
        if (self.input_file is None) == (self.json_body is None):
            raise ValueError("exactly one of -i/--input or -j/--json is required")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Command line only. The short aliases would otherwise also be read from
        # environment variables, and a stray H or HOST would silently change
        # the target server.
        return (init_settings,)


def build_parser() -> ArgumentParser:
    # The documented usage gives -h to --host, so help is --help only.
    parser = ArgumentParser(
        prog=PROG,
        description=(
            "Send a request to the caching service N times. Each iteration "
            "creates the payload and reads it back; results are written as JSON "
            "lines, a summary goes to stderr."
        ),
        epilog="Exit status: 0 on success, 1 if the service fails, "
        "2 on invalid arguments or input.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help and exit")
    return parser


def _add_argument(parser: ArgumentParser, *names: str, **kwargs: Any) -> Any:
    long_name = next((name for name in names if name.startswith("--")), "")
    if long_name in _METAVARS:
        kwargs["metavar"] = _METAVARS[long_name]
    return parser.add_argument(*names, **kwargs)


def describe_errors(error: ValidationError) -> str:
    """Render validation errors as one line, naming flags instead of fields."""
    flags = {}
    for field in CliSettings.model_fields.values():
        if isinstance(field.validation_alias, AliasChoices):
            short, long = field.validation_alias.choices
            flags[str(short)] = f"-{short}/--{long}"
    return "; ".join(_describe(detail, flags) for detail in error.errors())


def _describe(detail: ErrorDetails, flags: dict[str, str]) -> str:
    message = detail["msg"].removeprefix("Value error, ")
    location = [str(part) for part in detail["loc"]]
    if location and location[0] in flags:
        location[0] = f"argument {flags[location[0]]}"
    return ": ".join([*location, message])


def parse_args(argv: Sequence[str] | None = None) -> CliSettings:
    """Parse and validate the command line; exits with status 2 if it is invalid."""
    parser = build_parser()
    source = CliSettingsSource[CliSettings](
        CliSettings, root_parser=parser, add_argument_method=_add_argument
    )
    try:
        return CliApp.run(
            CliSettings,
            cli_args=None if argv is None else list(argv),
            cli_settings_source=source,
        )
    except ValidationError as error:
        parser.error(describe_errors(error))
