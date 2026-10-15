# backend/scripts/generate_openapi_schema.py
# Step 4.4-follow-up [SECURITY-adjacent]: kickoff decision #9 (Felix's
# §11.3 parallel-track protocol, 2026-10-15) -- "generate the OpenAPI
# schema to a committed file, and add a test that fails if the app's
# schema drifts from it." The committed file (docs/openapi.json) IS the
# locked API contract the widget and dashboard tracks both build
# against once Felix starts the two parallel worktrees -- changing the
# contract during those tracks is stop condition (a), per the protocol
# recorded in PROJECT_SPEC.md §11.5.
#
# No real database/Qdrant/SMTP connection needed: FastAPI's own
# app.openapi() is a pure, static introspection of route definitions --
# it never executes a handler body. A minimal, VALID (not real) env is
# constructed here, standalone, rather than importing tests.conftest.
# VALID_ENV -- this project's own established layering rule ("scripts/
# must not import test-only fixtures", Task 3.7.c's own duplication-
# check decision) applies here exactly as it does to every other
# scripts/ file.
#
# Deterministic by construction, confirmed live before relying on it:
# FastAPI's own schema generation has no randomness, no timestamps, and
# no environment-dependent ordering (route registration order in
# app/main.py's create_app() is fixed) -- re-running this script with
# no real API change produces byte-identical output, which is exactly
# what tests/test_openapi_contract.py's own drift check depends on.
import json
import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCHEMA_PATH = _BACKEND_DIR.parent / "docs" / "openapi.json"

# The minimal set of Settings fields with no usable default -- see
# app/config.py's own REQUIRED_VARS-shaped fields. Values are
# placeholders only (never used to make a real connection; app.openapi()
# never touches the database, Qdrant, or SMTP).
_MINIMAL_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "placeholder-qdrant-key",
    "QDRANT_READ_ONLY_API_KEY": "placeholder-qdrant-readonly-key",
    "JWT_SIGNING_KEY": "placeholder-jwt-signing-key-at-least-32-chars",
    "DB_CONNECTION_ENCRYPTION_KEY": "placeholder-db-connection-key-at-least-32-chars",
    "ADMIN_API_KEY": "placeholder-admin-key-at-least-32-characters-long",
    "OPENAI_API_KEY": "placeholder-openai-key",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}


def generate_schema() -> dict:
    # Imported lazily, after the env is set below -- app.config.get_
    # settings() reads os.environ at call time, and this script's own
    # caller (main(), below) must control exactly what that environment
    # looks like before any app.* import triggers a Settings() build.
    from app.main import create_app

    app = create_app()
    return app.openapi()


def main() -> None:
    for key, value in _MINIMAL_ENV.items():
        os.environ.setdefault(key, value)
    schema = generate_schema()
    _SCHEMA_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {_SCHEMA_PATH}")


if __name__ == "__main__":
    sys.exit(main())
