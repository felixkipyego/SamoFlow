# backend/tests/test_agent_entrypoint_guard.py
# Task 3.2.c [SECURITY]: the guarded entry point's own AST guard --
# "only one function may call graph.astream, aget_state or Command(resume=
# ...). It builds the config itself" (docs/SPEC.md §4.5). Matches
# test_qdrant_read_path_guard.py's/test_database_adapter_guard.py's/
# test_ingest_repository_guard.py's/test_config_guard.py's/test_audit_log_
# write_path_guard.py's own established style and NOTE-comment allow-list
# convention -- the sixth such guard in this codebase.
#
# Design, three layers protecting the actual invariant -- a LangGraph
# thread config is built in exactly ONE place -- rather than a method-name
# denylist:
#
# A first design attempt here tried to ban astream/ainvoke/invoke/stream/
# astream_events by name, the same shape as test_qdrant_read_path_guard.py's
# rule (b). Found wrong before building it further, not shipped and later
# discovered broken: unlike "retrieve"/"scroll" on a real Qdrant client
# (gated behind that guard's own rule (a) import confinement, so an
# unrelated same-named method elsewhere never trips it), `invoke`/
# `ainvoke`/`stream`/`astream` are universal `Runnable` methods shared by
# every chat model, chain and tool in the LangChain ecosystem -- and there
# is no equivalent import to gate behind here, since `CompiledStateGraph`/
# `Command` are legitimate imports anywhere in `app/agent/` once Step 3.3
# builds real graph/node code. An unscoped name ban on those five would
# have permanently false-positived the moment a future answer node called
# `llm.ainvoke(...)` on a chat model -- a real, not hypothetical, landmine
# for Step 3.3/3.4. Dropped entirely; the two layers below (plus a live
# runtime backstop in test_agent_entrypoint.py) cover the identical hazard
# without that collision:
#
#   1. Unscoped ban outside the entry module on the UNAMBIGUOUS LangGraph
#      state methods only -- aget_state/get_state/aupdate_state/
#      update_state/aget_state_history/get_state_history are not shared by
#      ordinary Runnables (confirmed: nothing else in this project's own
#      dependency tree exposes a same-named method) -- plus Command(...)
#      constructed with a resume= keyword specifically (Command(goto=...)
#      stays allowed everywhere, Step 3.3's own ordinary node-return value
#      for internal control flow).
#   2. Unscoped ban outside the entry module on CONFIG-KEY LITERALS: any
#      dict literal whose keys include "thread_id"/"checkpoint_id"/
#      "checkpoint_ns"/"configurable", and any call with a `configurable=`
#      keyword argument. Grepped against the real app/ tree before
#      finalizing this rule (not allow-listed blindly): zero existing
#      hits anywhere outside app/agent/entrypoint.py itself.
#   3. A live runtime backstop (test_agent_entrypoint.py, not here -- it
#      needs a real compiled graph + a real AsyncPostgresSaver): a graph
#      invoked with no thread config genuinely raises, confirmed against
#      the installed langgraph directly, not assumed. This is the fact
#      layers 1+2 rest on -- any code path that bypasses
#      _build_thread_config() has no way to supply a valid config without
#      writing one of the literals layer 2 already catches.
#
# Known, honestly-stated limitation (the same one every name/literal-based
# guard in this codebase already accepts, not a new one invented here):
# a deliberately, dynamically-built key string (e.g.
# `cfg[("conf" + "igurable")] = ...` or `key = "configurable"; d[key] = {}`)
# evades layer 2's static literal match. No type inference, no data-flow
# analysis is attempted -- the live runtime backstop (layer 3) is what
# actually catches a bypass of that shape, since it would still produce a
# config missing the real checkpointer keys.
import ast
from pathlib import Path

from tests.conftest import (
    BACKEND_DIR,
    check_call_allowlist,
    iter_python_files,
    write_guard_tree_file,
)

APP_DIR = BACKEND_DIR / "app"

# NOTE: adding a file here is a deliberate decision -- it means that file
# may call a LangGraph state method, construct Command(resume=...), or
# build a thread-config dict literal. Review it by hand.
ALLOWED_GRAPH_RUN_CALLERS: frozenset[str] = frozenset({"app/agent/entrypoint.py"})

# Layer 1: every public STATE method on the installed langgraph==1.2.14's
# CompiledStateGraph that is NOT also a generic Runnable method -- confirmed
# by introspecting the installed class directly at this task's own
# research step. Deliberately excludes astream/ainvoke/invoke/stream/
# astream_events -- see this file's own header comment for why.
FORBIDDEN_STATE_METHODS: tuple[str, ...] = (
    "aget_state",
    "get_state",
    "aupdate_state",
    "update_state",
    "aget_state_history",
    "get_state_history",
)

# Layer 2: the exact `configurable` sub-keys AsyncPostgresSaver's own real
# error message names (confirmed live, Task 3.2.c: "Checkpointer requires
# one or more of the following 'configurable' keys: thread_id,
# checkpoint_ns, checkpoint_id"), plus "configurable" itself (the dict key
# that wraps them in a real RunnableConfig).
CONFIG_DICT_KEYS: frozenset[str] = frozenset(
    {"thread_id", "checkpoint_id", "checkpoint_ns", "configurable"}
)


def _command_resume_violations(path: Path, rel: str) -> list[str]:
    if rel in ALLOWED_GRAPH_RUN_CALLERS:
        return []
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Command"
            and any(kw.arg == "resume" for kw in node.keywords)
        ):
            violations.append(
                f"{path}:{node.lineno}: constructs Command(resume=...) but {rel!r} "
                "is not in ALLOWED_GRAPH_RUN_CALLERS"
            )
    return violations


def _config_literal_violations(path: Path, rel: str) -> list[str]:
    if rel in ALLOWED_GRAPH_RUN_CALLERS:
        return []
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in CONFIG_DICT_KEYS
                ):
                    violations.append(
                        f"{path}:{node.lineno}: builds a dict literal with key "
                        f"{key.value!r} but {rel!r} is not in ALLOWED_GRAPH_RUN_CALLERS"
                    )
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "configurable":
                    violations.append(
                        f"{path}:{node.lineno}: passes configurable=... as a keyword "
                        f"argument but {rel!r} is not in ALLOWED_GRAPH_RUN_CALLERS"
                    )
    return violations


def scan_entrypoint_guard(root: Path) -> list[str]:
    """Pure static AST scan, no IO beyond reading the given tree's own .py
    files. `root` is the directory ALLOWED_GRAPH_RUN_CALLERS' paths are
    relative to (BACKEND_DIR in production; a tmp_path synthetic tree in
    tests below), matching scan_read_path_guard()'s own established shape.
    """
    app_root = root / "app"
    if not app_root.is_dir():
        return []

    paths = list(iter_python_files(app_root))
    violations: list[str] = []
    for method_name in FORBIDDEN_STATE_METHODS:
        violations.extend(
            check_call_allowlist(paths, method_name, tuple(ALLOWED_GRAPH_RUN_CALLERS), root=root)
        )
    for path in paths:
        rel = path.relative_to(root).as_posix()
        violations.extend(_command_resume_violations(path, rel))
        violations.extend(_config_literal_violations(path, rel))
    return violations


def test_entrypoint_guard_passes_against_the_real_backend_app():
    assert scan_entrypoint_guard(BACKEND_DIR) == []


# --- Layer 1: unambiguous state methods + Command(resume=...) -------------


def test_fails_on_every_forbidden_state_method_outside_the_allowlist(tmp_path):
    body = "\n".join(f"    graph.{name}(1)" for name in FORBIDDEN_STATE_METHODS)
    content = f"def f(graph):\n{body}\n"
    write_guard_tree_file(tmp_path, "app/other.py", content)
    violations = scan_entrypoint_guard(tmp_path)

    violated_methods = {
        name for name in FORBIDDEN_STATE_METHODS if any(f"calls {name}()" in v for v in violations)
    }
    assert violated_methods == set(FORBIDDEN_STATE_METHODS)
    assert len(violations) == len(FORBIDDEN_STATE_METHODS)


def test_passes_for_every_forbidden_state_method_inside_the_allowlisted_file(tmp_path):
    body = "\n".join(f"    graph.{name}(1)" for name in FORBIDDEN_STATE_METHODS)
    write_guard_tree_file(tmp_path, "app/agent/entrypoint.py", f"def f(graph):\n{body}\n")
    assert scan_entrypoint_guard(tmp_path) == []


def test_fails_on_command_resume_outside_the_allowlist(tmp_path):
    write_guard_tree_file(
        tmp_path,
        "app/other.py",
        'from langgraph.types import Command\n\n\ndef f():\n    return Command(resume="x")\n',
    )
    violations = scan_entrypoint_guard(tmp_path)
    assert len(violations) == 1
    assert "Command(resume=...)" in violations[0]


def test_passes_for_command_resume_inside_the_allowlisted_file(tmp_path):
    write_guard_tree_file(
        tmp_path,
        "app/agent/entrypoint.py",
        'from langgraph.types import Command\n\n\ndef f():\n    return Command(resume="x")\n',
    )
    assert scan_entrypoint_guard(tmp_path) == []


def test_passes_for_command_without_resume_anywhere(tmp_path):
    # Control: Command(update=...)/Command(goto=...) (no resume kwarg) are
    # ordinary node-return values once Step 3.3 lands -- never restricted,
    # anywhere, by this guard.
    write_guard_tree_file(
        tmp_path,
        "app/other.py",
        "from langgraph.types import Command\n\n\n"
        'def f():\n    return Command(update={"a": 1}, goto="node")\n',
    )
    assert scan_entrypoint_guard(tmp_path) == []


# --- Layer 2: config-key literals -------------------------------------------


def test_fails_on_a_dict_literal_with_each_config_key_outside_the_allowlist(tmp_path):
    content = "\n".join(
        f'x{i} = {{"{key}": "v"}}' for i, key in enumerate(sorted(CONFIG_DICT_KEYS))
    )
    write_guard_tree_file(tmp_path, "app/other.py", content + "\n")
    violations = scan_entrypoint_guard(tmp_path)

    violated_keys = {key for key in CONFIG_DICT_KEYS if any(repr(key) in v for v in violations)}
    assert violated_keys == CONFIG_DICT_KEYS
    assert len(violations) == len(CONFIG_DICT_KEYS)


def test_passes_for_the_same_dict_literals_inside_the_allowlisted_file(tmp_path):
    content = "\n".join(
        f'x{i} = {{"{key}": "v"}}' for i, key in enumerate(sorted(CONFIG_DICT_KEYS))
    )
    write_guard_tree_file(tmp_path, "app/agent/entrypoint.py", content + "\n")
    assert scan_entrypoint_guard(tmp_path) == []


def test_fails_on_a_configurable_keyword_argument_outside_the_allowlist(tmp_path):
    # `cfg` is a bare name, not a dict literal, so only the configurable=
    # keyword rule fires here -- isolated from the dict-literal rule above.
    write_guard_tree_file(
        tmp_path,
        "app/other.py",
        "def f(x, cfg):\n    return x(configurable=cfg)\n",
    )
    violations = scan_entrypoint_guard(tmp_path)
    assert len(violations) == 1
    assert "configurable=..." in violations[0]


def test_passes_for_an_unrelated_dict_literal_with_no_config_keys(tmp_path):
    # Control: an ordinary dict literal using unrelated keys is never
    # flagged -- this guard matches the exact key strings only, not "any
    # dict literal anywhere".
    write_guard_tree_file(tmp_path, "app/other.py", 'x = {"name": "a", "value": 1}\n')
    assert scan_entrypoint_guard(tmp_path) == []


# --- The false positive this design specifically avoids ---------------------


def test_passes_for_an_unrelated_llm_ainvoke_call_with_a_config_keyword(tmp_path):
    # The exact scenario the dropped name-based design would have broken:
    # a chat model's own ordinary .ainvoke() call, even passed a `config=`
    # keyword (not `configurable=`) -- a real, expected Step 3.3/3.4 shape,
    # confirmed here to pass cleanly, not just reasoned about.
    write_guard_tree_file(
        tmp_path,
        "app/other.py",
        "async def f(llm, messages, config):\n"
        "    return await llm.ainvoke(messages, config=config)\n",
    )
    assert scan_entrypoint_guard(tmp_path) == []
