# backend/tests/test_qdrant_read_path_guard.py
# Task 1.3.e: read-path guard -- the structural half of "all Qdrant reads go
# through retrieve()" (PROJECT_SPEC.md's engineering rules, referencing
# docs/SPEC.md §21). Static, AST-based, matching test_config_guard.py's
# style and its NOTE-comment allow-list convention.
#
# Design: import confinement (rule a) is the PRIMARY guard, not a
# method-name search. docs/SPEC.md names the function retrieve(), and
# qdrant-client's AsyncQdrantClient also has a method literally named
# retrieve -- a name-only scan would wrongly flag legitimate application
# code doing "from app.retrieval.service import retrieve" or
# "service.retrieve(...)". A file that cannot import qdrant_client or
# obtain a client at all cannot call a read method on one, so rule (a) does
# the heavy lifting; rule (b) (read-method calls) only ever applies to the
# handful of files rule (a) already lets touch a client -- everywhere else,
# a bare `retrieve(...)` or `service.retrieve(...)` call is just an ordinary
# function/attribute call on unrelated application code and must pass.
#
# Limits of this guard (see also the header comment above): it is static
# and name/import based. It cannot see getattr() with a computed method
# name, importlib tricks that avoid a literal "qdrant_client" string, or
# code that speaks raw HTTP to the Qdrant URL via httpx instead of the
# qdrant_client library. It complements the isolation tests Step 3.1 must
# run THROUGH retrieve() (test_qdrant_isolation.py); it does not replace
# them -- this guard proves *only* that no other file in backend/app can
# reach a read method at all, not that retrieve() itself filters correctly.
# A client object passed as a function argument into a file with no client
# access is invisible to this guard -- it can only happen at a call site in
# allow-listed code, which review must catch.
import ast
import inspect
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from tests.conftest import BACKEND_DIR, called_name, iter_python_files

APP_DIR = BACKEND_DIR / "app"

# NOTE: adding a file here is a deliberate decision -- it means that file may
# import qdrant_client and obtain a real Qdrant client (get_qdrant_client()/
# build_qdrant_client()). Review it by hand. (Step 2.5's ingestion writer
# will be added here deliberately -- see the open marker in
# PROJECT_SPEC.md, owned by Step 2.5, that it must use write methods only.)
# Task 1.4.k: app/main.py added -- the lifespan hook calls
# get_qdrant_client().close() during shutdown, a CONFIG method (see
# CONFIG_METHODS below), not a READ, so app/main.py does NOT also need
# READ_ALLOWLIST membership -- the same shape app/qdrant.py itself already
# has (client access without read access).
CLIENT_ACCESS_ALLOWLIST: frozenset[str] = frozenset(
    {"app/qdrant.py", "app/retrieval/service.py", "app/main.py"}
)

# NOTE: adding a file here is a deliberate decision -- it means that file may
# call a method classified as READ below. Review it by hand.
READ_ALLOWLIST: frozenset[str] = frozenset({"app/retrieval/service.py"})

# Checked in EVERY file under backend/app, regardless of the allow-lists
# above: these attributes expose the client's raw HTTP/gRPC transport
# objects (or, for `_client`, the qdrant_client library's own inner client),
# each of which can reach a read endpoint under a name the classification
# below never sees. Going around the classified public API this way would
# defeat rule (b) entirely, so it is blocked everywhere, not just outside
# the allow-lists.
RAW_CLIENT_SURFACE_ATTRS: frozenset[str] = frozenset(
    {"http", "grpc_points", "grpc_collections", "grpc_snapshots", "_client"}
)

# The app.qdrant module, as a dotted-name tuple: the target every import
# form below resolves against, whether written as an absolute import, a
# "from app import qdrant" package-attribute import, or a relative import
# ("from . import qdrant" / "from .qdrant import X" / "from ..qdrant import
# X") -- any form that ends up naming this exact module, regardless of what
# is imported *from* it, counts: get_qdrant_client()/build_qdrant_client()
# live there, but so would any future helper added to that file.
_APP_QDRANT_MODULE: tuple[str, ...] = ("app", "qdrant")

# --- Classification of every public AsyncQdrantClient method -------------
# Reviewed by hand against the installed qdrant-client==1.19.1 (introspected
# below, not assumed): every public (non-underscore) callable on
# AsyncQdrantClient must appear in exactly one of these three sets --
# enforced by test_classification_is_complete_disjoint_and_current().
#
# READ: returns stored points, point-derived counts/aggregates, or
# search/query results over points. WRITE: mutates point, payload or vector
# data. CONFIG: collection/index/alias/snapshot/cluster administration and
# inspection, plus embedding-model metadata utilities unrelated to any
# collection's stored points (get_embedding_size, list_*_models). When
# unsure whether something is a read, it is classified READ here (fail
# safe on the security-relevant classification, per the task's own
# instruction).
#
# Note: qdrant-client 1.19.1 has no bare `search`/`recommend`/`discover`
# method at all -- those REST operations are unified into query_points()
# (its `query` argument accepts a NearestQuery/RecommendQuery/DiscoverQuery
# etc.), confirmed live via hasattr(). The illustrative method names in
# this task's own design ("search*", "recommend*", "discover*") describe an
# older client surface that no longer exists on the pinned version; the
# table below reflects what is actually installed.
READ_METHODS: frozenset[str] = frozenset(
    {
        "count",
        "facet",
        # unimplemented on the async client today (raises NotImplementedError
        # -- confirmed by reading its source), but its intended semantics
        # (and the sync QdrantClient's real implementation) read every
        # point of the source collection(s) to copy them elsewhere.
        "migrate",
        "query_batch_points",
        "query_points",
        "query_points_groups",
        "retrieve",
        "scroll",
        "search_matrix_offsets",
        "search_matrix_pairs",
    }
)

WRITE_METHODS: frozenset[str] = frozenset(
    {
        "batch_update_points",
        "clear_payload",
        "delete",
        "delete_payload",
        "delete_vectors",
        "overwrite_payload",
        "set_payload",
        "update_vectors",
        "upload_collection",
        "upload_points",
        "upsert",
    }
)

CONFIG_METHODS: frozenset[str] = frozenset(
    {
        "close",
        "cluster_collection_update",
        "cluster_status",
        "cluster_telemetry",
        "collection_cluster_info",
        "collection_exists",
        "create_collection",
        "create_full_snapshot",
        "create_payload_index",
        "create_shard_key",
        "create_shard_snapshot",
        "create_snapshot",
        "create_vector_name",
        "delete_collection",
        "delete_full_snapshot",
        "delete_payload_index",
        "delete_shard_key",
        "delete_shard_snapshot",
        "delete_snapshot",
        "delete_vector_name",
        "get_aliases",
        "get_collection",
        "get_collection_aliases",
        "get_collections",
        "get_embedding_size",
        "get_optimizations",
        "info",
        "list_full_snapshots",
        "list_image_models",
        "list_late_interaction_multimodal_models",
        "list_late_interaction_text_models",
        "list_shard_keys",
        "list_shard_snapshots",
        "list_snapshots",
        "list_sparse_models",
        "list_text_models",
        "recover_current_peer",
        "recover_shard_snapshot",
        "recover_snapshot",
        "recreate_collection",
        "remove_peer",
        "update_collection",
        "update_collection_aliases",
    }
)

# --- Rule (d): forbidden everywhere in backend/app, with NO allow-list at
# all (not even app/qdrant.py) -------------------------------------------
# Snapshots (create_snapshot/create_full_snapshot/create_shard_snapshot,
# recover_snapshot/recover_shard_snapshot) are an unfiltered, whole-database
# bulk export or import path: a snapshot dumps every tenant's raw points
# with no client_id filtering at all, and restoring one writes that data
# back wholesale -- neither operation is reachable through tenant_filter().
# Qdrant is *derived* from Postgres (docs/SPEC.md §5.2), so it can always be
# rebuilt by re-indexing from the source of truth; this project has no need
# for a Qdrant snapshot. recreate_collection() and delete_collection() would
# destroy every tenant's data at once -- ensure_collection() (Task 1.3.c)
# deliberately never calls either, verifying and repairing an existing
# collection in place instead of ever dropping it. Adding an entry here is
# a deliberate decision, exactly like the other allow-lists above -- except
# there is deliberately no allow-list at all for this one; a future genuine
# need for any of these belongs in a separate, reviewed admin/ops tool, not
# general application code.
FORBIDDEN_IN_APP: frozenset[str] = frozenset(
    {
        "create_full_snapshot",
        "create_shard_snapshot",
        "create_snapshot",
        "delete_collection",
        "recover_shard_snapshot",
        "recover_snapshot",
        "recreate_collection",
    }
)


def _public_async_client_methods() -> frozenset[str]:
    return frozenset(
        name
        for name, _ in inspect.getmembers(AsyncQdrantClient, predicate=callable)
        if not name.startswith("_")
    )


def test_classification_is_complete_disjoint_and_current():
    installed = _public_async_client_methods()
    classified = READ_METHODS | WRITE_METHODS | CONFIG_METHODS

    unclassified = installed - classified
    assert not unclassified, (
        f"AsyncQdrantClient gained new public method(s) not classified in "
        f"test_qdrant_read_path_guard.py: {sorted(unclassified)}. Classify "
        "each as READ, WRITE or CONFIG there (this means qdrant-client was "
        "upgraded)."
    )

    stale = classified - installed
    assert not stale, (
        f"test_qdrant_read_path_guard.py classifies method(s) that no "
        f"longer exist on AsyncQdrantClient: {sorted(stale)}. Re-classify "
        "after this qdrant-client upgrade."
    )

    overlaps = []
    for set_a, set_b, name_a, name_b in (
        (READ_METHODS, WRITE_METHODS, "READ", "WRITE"),
        (READ_METHODS, CONFIG_METHODS, "READ", "CONFIG"),
        (WRITE_METHODS, CONFIG_METHODS, "WRITE", "CONFIG"),
    ):
        both = set_a & set_b
        if both:
            overlaps.append(f"{sorted(both)} classified as both {name_a} and {name_b}")
    assert not overlaps, "; ".join(overlaps)


def test_forbidden_in_app_methods_exist_and_are_classified():
    installed = _public_async_client_methods()
    classified = READ_METHODS | WRITE_METHODS | CONFIG_METHODS

    missing = FORBIDDEN_IN_APP - installed
    assert not missing, (
        f"FORBIDDEN_IN_APP names method(s) that no longer exist on "
        f"AsyncQdrantClient: {sorted(missing)}. Re-check after a "
        "qdrant-client upgrade."
    )

    unclassified = FORBIDDEN_IN_APP - classified
    assert not unclassified, (
        f"FORBIDDEN_IN_APP method(s) not classified in exactly one of "
        f"READ/WRITE/CONFIG: {sorted(unclassified)}. Every forbidden "
        "method must also have a real classification."
    )


# --- The guard itself -------------------------------------------------------


def _package_parts(rel: str) -> tuple[str, ...]:
    # The dotted package a relative import inside this file resolves
    # against -- Python's own __package__ semantics: the dotted form of the
    # file's containing directory, identical whether the file is a regular
    # module or its package's own __init__.py (in both cases __package__ is
    # the containing directory's dotted path, never the file's own name).
    return tuple(rel.split("/")[:-1])


def _plain_import_targets_app_qdrant(alias: ast.alias) -> bool:
    # "import app.qdrant" / "import app.qdrant as aq" -- always absolute,
    # no relative form exists for plain `import`.
    return alias.name == "app.qdrant"


def _import_from_targets_app_qdrant(node: ast.ImportFrom, rel: str) -> bool:
    if node.level == 0:
        module = node.module or ""
        if module == "app.qdrant":
            return True  # from app.qdrant import <anything>
        if module == "app":
            # from app import qdrant  (alongside possibly other names)
            return any(alias.name == "qdrant" for alias in node.names)
        return False

    # Relative import ("from . import qdrant" / "from .qdrant import X" /
    # "from ..qdrant import X"): resolve against this file's own package,
    # walking up one level per extra dot beyond the first.
    package = _package_parts(rel)
    if node.level - 1 > len(package):
        return False  # would resolve above the scanned tree's own root
    base = package[: len(package) - (node.level - 1)]
    if node.module:
        target = base + tuple(node.module.split("."))
        return target == _APP_QDRANT_MODULE
    # "from . import qdrant" (module is None): each imported name is looked
    # up directly inside the resolved base package.
    return any(base + (alias.name,) == _APP_QDRANT_MODULE for alias in node.names)


def _import_violations(node: ast.AST, rel: str, has_client_access: bool) -> list[str]:
    if has_client_access:
        return []
    violations = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == "qdrant_client" or alias.name.startswith("qdrant_client."):
                violations.append(
                    f"{rel}:{node.lineno}: imports {alias.name!r}; only "
                    f"{sorted(CLIENT_ACCESS_ALLOWLIST)} may import qdrant_client "
                    "(rule a: import confinement)"
                )
            elif _plain_import_targets_app_qdrant(alias):
                violations.append(
                    f"{rel}:{node.lineno}: imports {alias.name!r}; only "
                    f"{sorted(CLIENT_ACCESS_ALLOWLIST)} may import app.qdrant "
                    "(rule a: import confinement)"
                )
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        from_qdrant_client_pkg = module == "qdrant_client" or module.startswith("qdrant_client.")
        if from_qdrant_client_pkg:
            for alias in node.names:
                violations.append(
                    f"{rel}:{node.lineno}: imports {alias.name!r} from {module!r}; "
                    f"only {sorted(CLIENT_ACCESS_ALLOWLIST)} may import qdrant_client "
                    "(rule a: import confinement)"
                )
        elif _import_from_targets_app_qdrant(node, rel):
            violations.append(
                f"{rel}:{node.lineno}: imports from app.qdrant "
                f"(module={module!r}, level={node.level}); only "
                f"{sorted(CLIENT_ACCESS_ALLOWLIST)} may import app.qdrant "
                "(rule a: import confinement)"
            )
    return violations


def _dynamic_import_violations(node: ast.AST, rel: str, has_client_access: bool) -> list[str]:
    # importlib.import_module("qdrant_client") / __import__("qdrant_client")
    # with a literal string argument -- per the task's own stated limits,
    # this cannot see a computed/non-literal module name.
    if not isinstance(node, ast.Call) or has_client_access:
        return []
    name = called_name(node)
    if name not in ("import_module", "__import__") or not node.args:
        return []
    first_arg = node.args[0]
    if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
        return []
    target = first_arg.value
    if target == "qdrant_client" or target.startswith("qdrant_client."):
        return [
            f"{rel}:{node.lineno}: dynamically imports {target!r} via "
            f"{name}(); only {sorted(CLIENT_ACCESS_ALLOWLIST)} may import "
            "qdrant_client (rule a: import confinement)"
        ]
    return []


def _raw_surface_violations(node: ast.AST, rel: str) -> list[str]:
    # Rule (c): checked in every file, regardless of the allow-lists.
    if isinstance(node, ast.Attribute) and node.attr in RAW_CLIENT_SURFACE_ATTRS:
        return [
            f"{rel}:{node.lineno}: accesses {node.attr!r}, a raw client transport "
            "surface that bypasses the read/write/config classification "
            "(rule c: no raw API surfaces)"
        ]
    return []


def _read_call_violations(
    node: ast.AST, rel: str, has_client_access: bool, may_read: bool
) -> list[str]:
    # Rule (b): only ever applies to files with client access -- a file with
    # no client access cannot possibly hold a real client to call a read
    # method on, so an attribute call named e.g. "scroll" on that file's own
    # unrelated object is not a violation. (Trade-off stated explicitly, per
    # the task: this guard does not attempt type inference, so it cannot
    # tell a real Qdrant client apart from an unrelated same-named method on
    # some other object -- it only needs to, because rule (a) already
    # confines which files can possibly hold a real client.)
    if not has_client_access or may_read:
        return []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in READ_METHODS:
            return [
                f"{rel}:{node.lineno}: calls {node.func.attr}(), a method classified "
                f"READ, but {rel!r} is not in READ_ALLOWLIST (rule b: reads confined "
                "to the allow-listed file)"
            ]
    return []


def _forbidden_method_violations(node: ast.AST, rel: str) -> list[str]:
    # Rule (d): checked in EVERY file, with no allow-list at all -- unlike
    # rules (a)/(b), CLIENT_ACCESS_ALLOWLIST/READ_ALLOWLIST membership is
    # never consulted here, so even app/qdrant.py itself is forbidden from
    # calling any of these (see FORBIDDEN_IN_APP's own comment for why).
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in FORBIDDEN_IN_APP:
            return [
                f"{rel}:{node.lineno}: calls {node.func.attr}(), forbidden in "
                "application code everywhere -- snapshots are an unfiltered "
                "cross-tenant bulk export/import path, and "
                "recreate_collection/delete_collection would destroy every "
                "tenant's data (rule d: no snapshots, no destructive "
                "collection operations)"
            ]
    return []


def scan_read_path_guard(root: Path) -> list[str]:
    """Pure static AST scan, no IO beyond reading the given tree's own .py
    files. `root` is the directory the allow-lists' paths are relative to
    (BACKEND_DIR in production; a tmp_path synthetic tree in tests below),
    so the guard is fully testable without touching the real backend/app
    tree.
    """
    violations: list[str] = []
    app_root = root / "app"
    if not app_root.is_dir():
        return violations
    for path in iter_python_files(app_root):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        has_client_access = rel in CLIENT_ACCESS_ALLOWLIST
        may_read = rel in READ_ALLOWLIST
        for node in ast.walk(tree):
            violations.extend(_import_violations(node, rel, has_client_access))
            violations.extend(_dynamic_import_violations(node, rel, has_client_access))
            violations.extend(_raw_surface_violations(node, rel))
            violations.extend(_read_call_violations(node, rel, has_client_access, may_read))
            violations.extend(_forbidden_method_violations(node, rel))
    return violations


def test_read_path_guard_passes_against_the_real_backend_app():
    # Nothing under backend/app reads from Qdrant yet (retrieve() is Step
    # 3.1's job) -- this must pass today, and stay passing as a regression
    # guard once retrieve() exists.
    assert scan_read_path_guard(BACKEND_DIR) == []


# --- Guard unit tests on synthetic trees (no Qdrant needed) -----------------


def _write(tmp_path: Path, rel_path: str, content: str) -> None:
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_fails_on_qdrant_client_import_outside_the_allowlist(tmp_path):
    _write(tmp_path, "app/other.py", "import qdrant_client\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_get_qdrant_client_import_outside_the_allowlist(tmp_path):
    _write(tmp_path, "app/other.py", "from app.qdrant import get_qdrant_client\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


# --- Tightened rule (a): ANY import of the app.qdrant module, in every
# form, fails outside CLIENT_ACCESS_ALLOWLIST -- not just a name-specific
# check for get_qdrant_client/build_qdrant_client. Each form below is
# tested both failing (unlisted file) and passing (the two allow-listed
# files), plus a control proving imports of *other* app modules are
# unaffected.


def test_fails_on_plain_import_of_app_qdrant(tmp_path):
    _write(tmp_path, "app/other.py", "import app.qdrant\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_from_app_import_qdrant(tmp_path):
    _write(tmp_path, "app/other.py", "from app import qdrant\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_from_app_qdrant_import_any_name(tmp_path):
    # Proves the check is no longer name-specific: ensure_collection (not
    # get_qdrant_client/build_qdrant_client) still trips it.
    _write(tmp_path, "app/other.py", "from app.qdrant import ensure_collection\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_single_dot_relative_import_of_qdrant(tmp_path):
    # "from . import qdrant" inside a file whose own package is "app"
    # (i.e. any top-level app/ file) resolves to app.qdrant.
    _write(tmp_path, "app/other.py", "from . import qdrant\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_single_dot_relative_from_qdrant_import(tmp_path):
    _write(tmp_path, "app/other.py", "from .qdrant import get_qdrant_client\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_double_dot_relative_import_from_a_subpackage(tmp_path):
    # A file one level deeper (app/util/helper.py) reaching app.qdrant via
    # two dots -- proves the level>1 walk-up resolution, not just level 1.
    _write(tmp_path, "app/util/helper.py", "from ..qdrant import get_qdrant_client\n")
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "app/util/helper.py" in violations[0]
    assert "rule a" in violations[0]


def test_passes_for_every_app_qdrant_import_form_inside_the_allowlisted_files(tmp_path):
    for rel_path in ("app/qdrant.py", "app/retrieval/service.py"):
        for content in (
            "import app.qdrant\n",
            "from app import qdrant\n",
            "from app.qdrant import ensure_collection\n",
        ):
            _write(tmp_path, rel_path, content)
            assert scan_read_path_guard(tmp_path) == [], (rel_path, content)


def test_passes_for_imports_of_other_app_modules_not_app_qdrant(tmp_path):
    _write(
        tmp_path,
        "app/other.py",
        "from app.retrieval.service import retrieve\nfrom app.config import get_settings\n",
    )
    assert scan_read_path_guard(tmp_path) == []


def test_fails_on_a_client_access_file_outside_read_allowlist_calling_query_points(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\ndef f(client):\n    return client.query_points(1)\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "query_points" in violations[0]
    assert "rule b" in violations[0]


def test_fails_on_a_different_client_access_file_calling_scroll(tmp_path):
    # A distinct file/method from the query_points case above, proving rule
    # (b) is checked per call site, not just once.
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\ndef f(client):\n    return client.scroll(1)\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "scroll" in violations[0]
    assert "rule b" in violations[0]


def test_fails_when_the_client_access_allowlisted_file_itself_calls_a_read_method(tmp_path):
    # app/qdrant.py IS in CLIENT_ACCESS_ALLOWLIST but NOT in READ_ALLOWLIST:
    # rule (b) still applies to it. Uses retrieve() specifically -- the one
    # name this whole guard exists to disambiguate from
    # app.retrieval.service's own retrieve() function (see the module
    # header comment) -- to prove the attribute-call shape is still caught
    # correctly even for that exact name.
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\ndef f(client):\n    return client.retrieve(1)\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "retrieve" in violations[0]
    assert "rule b" in violations[0]


def test_fails_on_the_raw_http_transport_surface(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\ndef f(client):\n    return client.http.points_api\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "'http'" in violations[0]
    assert "rule c" in violations[0]


def test_fails_on_the_raw_inner_client_surface(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\ndef f(client):\n    return client._client\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "'_client'" in violations[0]
    assert "rule c" in violations[0]


def test_passes_for_the_read_allowlisted_file_calling_query_points(tmp_path):
    _write(
        tmp_path,
        "app/retrieval/service.py",
        "import qdrant_client\n\n\ndef f(client):\n    return client.query_points(1)\n",
    )
    assert scan_read_path_guard(tmp_path) == []


def test_passes_for_qdrant_py_calling_config_methods(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\n"
        "async def f(client, name):\n"
        "    await client.get_collection(name)\n"
        "    await client.collection_exists(name)\n",
    )
    assert scan_read_path_guard(tmp_path) == []


def test_passes_for_a_bare_name_call_to_retrieve_with_no_client_access(tmp_path):
    _write(
        tmp_path,
        "app/chat/routes.py",
        "from app.retrieval.service import retrieve\n\n\ndef f():\n    return retrieve(1)\n",
    )
    assert scan_read_path_guard(tmp_path) == []


def test_passes_for_an_attribute_call_to_service_retrieve_with_no_client_access(tmp_path):
    _write(
        tmp_path,
        "app/chat/routes.py",
        "from app.retrieval import service\n\n\ndef f():\n    return service.retrieve(1)\n",
    )
    assert scan_read_path_guard(tmp_path) == []


def test_passes_for_an_unrelated_scroll_method_with_no_client_access(tmp_path):
    # Trade-off stated in _read_call_violations()'s own comment: rule (b)
    # applies only to files with client access. This file has none (no
    # qdrant_client import, no get_qdrant_client/build_qdrant_client
    # import), so its own unrelated object's .scroll() method -- e.g. a
    # pagination helper, nothing to do with Qdrant -- is not flagged. This
    # is a deliberate, accepted false-negative surface: rule (a) already
    # guarantees this file cannot hold a real Qdrant client, so there is
    # nothing here for rule (b) to protect against.
    _write(
        tmp_path,
        "app/chat/routes.py",
        "class Paginator:\n    def scroll(self, n):\n        return n\n\n\n"
        "def f():\n    return Paginator().scroll(1)\n",
    )
    assert scan_read_path_guard(tmp_path) == []


# --- Rule (d): FORBIDDEN_IN_APP has no allow-list at all -- checked even in
# app/qdrant.py itself, which is otherwise fully client-access- and
# READ_ALLOWLIST-exempt for rules (a)/(b).


def test_fails_on_create_snapshot_even_in_the_client_access_allowlisted_file(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\n"
        "async def f(client, name):\n"
        "    return await client.create_snapshot(collection_name=name)\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "create_snapshot" in violations[0]
    assert "rule d" in violations[0]


def test_fails_on_delete_collection_even_in_the_client_access_allowlisted_file(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\n"
        "async def f(client, name):\n"
        "    return await client.delete_collection(name)\n",
    )
    violations = scan_read_path_guard(tmp_path)
    assert len(violations) == 1
    assert "delete_collection" in violations[0]
    assert "rule d" in violations[0]


def test_passes_for_get_collection_collection_exists_and_list_snapshots(tmp_path):
    _write(
        tmp_path,
        "app/qdrant.py",
        "import qdrant_client\n\n\n"
        "async def f(client, name):\n"
        "    await client.get_collection(name)\n"
        "    await client.collection_exists(name)\n"
        "    await client.list_snapshots(name)\n",
    )
    assert scan_read_path_guard(tmp_path) == []
