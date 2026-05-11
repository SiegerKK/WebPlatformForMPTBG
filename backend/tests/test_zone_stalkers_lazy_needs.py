from __future__ import annotations

from app.games.zone_stalkers.needs.lazy_needs import (
    ensure_needs_state,
    get_need,
    materialize_needs,
    set_need,
)


def test_lazy_need_value_increases_with_world_turn():
    agent = {"hunger": 10.0, "thirst": 20.0, "sleepiness": 30.0}
    ensure_needs_state(agent, world_turn=100)

    hunger_100 = get_need(agent, "hunger", world_turn=100)
    hunger_160 = get_need(agent, "hunger", world_turn=160)
    assert hunger_160 > hunger_100


def test_materialize_needs_updates_legacy_fields():
    agent = {"hunger": 5.0, "thirst": 10.0, "sleepiness": 15.0}
    ensure_needs_state(agent, world_turn=50)

    values = materialize_needs(agent, world_turn=110)
    assert agent["hunger"] == values["hunger"]
    assert agent["thirst"] == values["thirst"]
    assert agent["sleepiness"] == values["sleepiness"]


def test_set_need_resets_base_and_bumps_revision():
    agent = {"hunger": 40.0, "thirst": 55.0, "sleepiness": 65.0}
    ensure_needs_state(agent, world_turn=10)
    old_revision = agent["needs_state"]["revision"]

    set_need(agent, "thirst", 12.0, world_turn=20)
    assert get_need(agent, "thirst", world_turn=20) == 12.0
    assert agent["thirst"] == 12.0
    assert agent["needs_state"]["revision"] == old_revision + 1
