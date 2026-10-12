# backend/tests/test_agent_context.py
# Task 3.3.a [SECURITY]: the one accessor graph nodes use to read
# tenant_id out of the LangGraph config (app/agent/context.py). Pure/
# offline -- no real graph or database needed to test a plain dict-in,
# uuid-or-raise-out function.
import uuid

import pytest

from app.agent.context import tenant_id_from_config


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
