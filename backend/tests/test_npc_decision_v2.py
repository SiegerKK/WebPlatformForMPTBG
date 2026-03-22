"""Backwards-compatibility shim.

All tests have been migrated to ``tests/decision/``.
This file re-imports from those modules so that any external tooling that
references ``tests/test_npc_decision_v2.py`` directly continues to work.
"""
from tests.decision.test_needs import *          # noqa: F401,F403
from tests.decision.test_intents import *        # noqa: F401,F403
from tests.decision.test_planner import *        # noqa: F401,F403
from tests.decision.test_integration import *    # noqa: F401,F403
