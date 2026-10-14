# payload-cache-service

A FastAPI microservice that builds payloads from two lists of strings. Every
string goes through a "transformer", which stands in for a slow external
service. Its results are cached in a database, so each distinct string is sent
to the transformer once, however many requests contain it. (With several
replicas that holds per replica; see the trade-offs.)

The repository also contains `cache-cli`, a command line client for exercising
the service.

## Quick start

You need Docker with Compose, and [uv](https://docs.astral.sh/uv/) to run the CLI.

```bash
docker compose up --build --wait     # Postgres, migrations, API on :8000

uv run cache-cli -h http://localhost:8000 -r 3 \
  -j '{"list_1": ["first string", "second string", "third string"],
       "list_2": ["other string", "another string", "last string"]}'

docker compose down --volumes
```

The first iteration waits for the transformer, the other two are served from
the cache:

```text
{"iteration":1,"id":"799d534f-…","created":true,"output":"FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING","create_ms":122.5,"read_ms":4.1}
{"iteration":2,"id":"799d534f-…","created":false,"output":"FIRST STRING, …","create_ms":5.7,"read_ms":1.4}
{"iteration":3,"id":"799d534f-…","created":false,"output":"FIRST STRING, …","create_ms":2.9,"read_ms":1.5}
3 iteration(s): 1 created, 2 reused; create took 2.9-122.5 ms (median 5.7 ms)
```

Interactive API docs are served at <http://localhost:8000/docs>.

The image also runs on its own. It then applies the migrations to a SQLite
database inside the container before it starts serving:

```bash
docker build -t payload-cache-service . && docker run -p 8000:8000 payload-cache-service
```

## API

### `POST /payload`

```json
{
  "list_1": ["first string", "second string", "third string"],
  "list_2": ["other string", "another string", "last string"]
}
```

Both lists must have the same length and contain 1 to 1000 strings. Each
string can be up to 1000 characters long and must not contain NUL characters,
which Postgres cannot store.

| Status | Meaning |
| --- | --- |
| `201 Created` | New payload. Body: `{"id": "<uuid>", "message": "Payload created"}`, plus a `Location` header pointing at it. |
| `200 OK` | The same payload was generated before; its id is returned with `"message": "Payload already exists"`. |
| `422 Unprocessable Content` | The body is invalid, e.g. the lists differ in length. |
| `502 Bad Gateway` | The transformer failed or timed out. Retrying is safe and only repeats the failed calls. |

### `GET /payload/{id}`

Returns the payload, `404` for an unknown id, `422` for a malformed one:

```json
{"output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"}
```

### `GET /health`

`{"status": "ok"}` when the database is reachable and migrated, `503`
otherwise. The Docker health check uses it.

## cache-cli

```text
usage: cache-cli [--help] [-h URL] [-r N] [-i FILE|-] [-j JSON] [-o FILE|-]
```

| Option | Default | |
| --- | --- | --- |
| `-h`, `--host URL` | `http://localhost:8000` | Base URL of the service. |
| `-r`, `--repeat N` | `1` | Number of iterations. |
| `-i`, `--input FILE\|-` | | Request body from a file, `-` reads stdin. |
| `-j`, `--json JSON` | | Request body as a JSON string. |
| `-o`, `--output FILE\|-` | `-` | Where to write the results, `-` is stdout. |
| `--help` | | Show the help. |

Exactly one of `--input` and `--json` is required. Each iteration creates the
payload and reads it back; the result is written as one JSON line, and a
summary goes to stderr. The exit status is 0 on success, 1 when the service
fails or cannot be reached, and 2 on invalid arguments or input, including an
output file that cannot be written.

```bash
uv run cache-cli -i request.json -o results.jsonl
cat request.json | uv run cache-cli -i - -r 10
```

The arguments are parsed and validated with Pydantic Settings
(`CliSettingsSource`): the URL must be valid, `N` positive, the input file must
exist, `--json` must be a valid request and the output directory must exist.
Invalid arguments are reported per flag, before anything is sent.

The specified usage gives `-h` to both `--host` and `--help`. That cannot
work, and with default settings argparse refuses to even build the parser. I
kept `-h` for `--host`, because it comes first in the usage line and a client
for a service mostly needs a host, and left help on `--help` only.

## How it works

What happens on `POST /payload`:

1. FastAPI validates the body against `PayloadCreate` (`schemas.py`).
2. `PayloadService` (`service.py`) collects the distinct strings of both lists.
   A string that occurs several times in one request is transformed once.
3. `TransformationCache` (`caching.py`) looks all of them up in one query and
   calls the transformer, concurrently, only for the misses. Each result is
   stored as soon as it arrives.
4. The transformed strings are interleaved and joined with `", "`.
5. `PayloadRepository` (`repositories.py`) returns the id of the payload with
   that content, inserting it if it is new.

`GET /payload/{id}` is a single primary key lookup.

### Keeping transformer calls to a minimum

The `cached_transformations` table maps every input string to its result, so a
string seen in any earlier request is never sent again. The table is keyed by
the SHA-256 of the input, which keeps the index small for long strings
(Postgres rejects B-tree entries above ~2.7 kB). Within one request only the
distinct strings are looked up and transformed.

Concurrent requests need more care, because two of them can miss the cache for
the same string at the same moment. In `TransformationCache`, a miss is resolved
by a single task that checks the cache once more, calls the transformer and
stores the result. Every request that misses the same string while that task
runs waits for it instead of calling the transformer again. The task is only
released after the result is committed, so a string is always either in flight
or cached. The same class caps the number of parallel calls and gives each call
a timeout, as a client of a real remote service would.

If some calls fail, the results of the others are stored anyway and the
request fails with `502`; a retry only repeats what failed. A request that is
cancelled while waiting does not cancel the call either, and its result is
still stored for the next request. On shutdown the service waits for such calls
before it closes the database.

### Payload identity

A payload is identified by its content: the `payloads` table has a unique
SHA-256 of the output. Repeating a request returns the existing id, and so does
a different request that produces the same output (`["a"], ["b"]` and
`["A"], ["B"]` both give `A, B`). This is the literal reading of "reuse the
payload identifier for payloads generated before". Ids are random UUIDs, so they
do not reveal the content and do not depend on the hashing scheme.

Keying payloads by request would only save the cache lookup on a repeated
request, which costs no transformer calls either way.

### Database access

SQLAlchemy 2 with the async engine (asyncpg for Postgres, aiosqlite for SQLite),
schema managed by Alembic. Every repository method runs in its own short
transaction rather than one per request, for two reasons:

- the service never holds a database connection while it waits for the
  transformer, so slow transformer calls cannot exhaust the connection pool;
- a cached result becomes visible to concurrent requests, and to other
  replicas, as soon as it is stored.

All writes are single-row `INSERT … ON CONFLICT DO NOTHING` statements. When
two requests, or two replicas, store the same string or payload at the same
moment, the database settles it and neither fails on a unique constraint.
Writing one row per statement also rules out deadlocks between transactions
that insert the same rows in a different order.

## Configuration

The service reads `CACHE_SERVICE_*` environment variables, or a `.env` file:

| Variable | Default | |
| --- | --- | --- |
| `CACHE_SERVICE_DATABASE_URL` | `sqlite+aiosqlite:///./cache.db` | SQLAlchemy URL; compose sets a Postgres one. |
| `CACHE_SERVICE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, in any case. |
| `CACHE_SERVICE_TRANSFORMER_LATENCY` | `0.1` | Simulated delay of the transformer, in seconds. |
| `CACHE_SERVICE_TRANSFORMER_TIMEOUT` | `5.0` | Timeout of one transformer call, in seconds. |
| `CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` | `10` | Parallel transformer calls per process. |

## Development

```bash
uv sync                                    # Python 3.14 and all dependencies

uv run alembic upgrade head                # SQLite database in ./cache.db
uv run uvicorn cache_service.main:create_app --factory --reload

uv run pytest --cov                        # tests, SQLite
uv run ruff check && uv run ruff format --check && uv run mypy
```

To run the integration tests against Postgres, point them at an empty database:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/test uv run pytest tests/integration
```

CI (`.github/workflows/ci.yml`) runs the linters and type checks, the test
suite on SQLite and on Postgres, and an end-to-end check that starts the compose
stack and runs `cache-cli` against it.

### Layout

```text
src/cache_service/
  main.py           app factory and lifespan (engine, transformer)
  api.py            HTTP endpoints
  schemas.py        request and response models
  service.py        payload generation and interleaving
  caching.py        transformer calls through the cache, at most one per string
  transformer.py    transformer protocol and the simulated service
  repositories.py   database access
  models.py         tables
  config.py         settings
src/cache_cli/
  settings.py       argument parsing and validation (Pydantic Settings)
  client.py         HTTP client
  main.py           entry point
migrations/         Alembic
tests/unit/         no I/O: schemas, caching, service, CLI parsing and HTTP client
tests/integration/  real database and HTTP: repositories, API, migrations, CLI against a live server
```

## Tests

Unit tests run without I/O. `TransformationCache` and the service are tested
with an in-memory store and a transformer that counts its calls, which is how
the "minimize calls" requirement is checked directly. The concurrency tests
control exactly when fake calls and writes finish, so they do not depend on
timing. The CLI's HTTP client is tested against `httpx.MockTransport`.

Integration tests use a real database (a temporary SQLite file, or Postgres in
CI):

- the repositories, including concurrent inserts of the same payload;
- the API through the ASGI stack, including concurrent requests, a restart
  with a warm cache, transformer failures and an unusable database;
- the migrations, compared against the models, so the two cannot drift apart;
- `cache-cli`, run against the service served by uvicorn on a local port,
  including its error paths.

## Trade-offs and shortcuts

- The transformer is simulated: it uppercases the string after a short sleep.
  A real client would implement the same `Transformer` protocol and raise
  `TransformerError` on failure; nothing else would change.
- The cache never expires, because the transformer is assumed to be
  deterministic. If its behaviour could change, its version should become part
  of the cache key.
- Sharing in-flight calls works within one process. Replicas that miss the
  same string at the same moment may each call the transformer once; the
  second result is discarded when it is stored. Avoiding that would take a
  distributed lock, which is not worth it here.
- The cap on parallel transformer calls is shared by all requests, so a
  request with many new strings delays the ones queued behind it. Giving each
  request a fair share of the calls would fix that, at the cost of a scheduler.
- The request limits are arbitrary. 1000 strings per list and 1000 characters
  per string keep the worst-case cost of one request bounded. In a real service
  they would come from the product requirements.
- The output format follows the spec. Strings are joined with `", "`, so a
  string that itself contains `", "` makes the output ambiguous.
- The CLI shares the request schema with the service. It rejects a bad
  request before sending it, which also means it cannot be used to send invalid
  requests; the integration tests cover server-side validation.
- Compose runs the migrations in a separate one-off container before the API
  starts. With several replicas that step belongs in the deployment pipeline;
  migrating on start, as the standalone image does, is only safe for a single
  instance.
- There is no authentication or rate limiting; both were out of scope.
