# backend/tests/test_agent_context.py
# Task 3.3.a [SECURITY]: the one accessor graph nodes use to read
# tenant_id out of the LangGraph config (app/agent/context.py). Pure/
# offline -- no real graph or database needed to test a plain dict-in,
# uuid-or-raise-out function.
import uuid

import pytest

from app.agent.context import support_text_from_config, tenant_id_from_config


def test_returns_the_real_tenant_id_from_a_well_formed_config():
    tenant_id = uuid.uuid4()
    config = {"configurable": {"thread_id": "t:v:c", "tenant_id": tenant_id}}
    assert tenant_id_from_config(config) == tenant_id


def test_raises_when_configurable_is_missing_entirely():
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_id_from_config({})


def test_raises_when_tenant_id_is_missing_from_configurable():
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_id_from_config({"configurable": {"thread_id": "t:v:c"}})


def test_raises_when_tenant_id_is_the_wrong_type():
    # A string, not a real uuid.UUID -- the same discipline
    # _build_thread_config() itself applies on the way in.
    config = {"configurable": {"tenant_id": str(uuid.uuid4())}}
    with pytest.raises(ValueError, match="tenant_id"):
        tenant_id_from_config(config)


# --- support_text_from_config() (Step 6.2) --------------------------


def test_support_text_from_config_unwraps_the_real_text():
    config = {"configurable": {"support_text": {"text": "Call +1 555 0100."}}}
    assert support_text_from_config(config) == "Call +1 555 0100."


def test_support_text_from_config_returns_none_with_no_contact_method():
    # The wrapper dict itself is still present (app.agent.entrypoint.
    # _build_thread_config() always stores one) -- its own "text" value
    # is None when the tenant has no contact method configured.
    config = {"configurable": {"support_text": {"text": None}}}
    assert support_text_from_config(config) is None


def test_support_text_from_config_returns_none_when_entirely_absent():
    # Every pre-Step-6.2 caller/test builds a config with no
    # "support_text" key at all.
    assert support_text_from_config({}) is None
    assert support_text_from_config({"configurable": {}}) is None
