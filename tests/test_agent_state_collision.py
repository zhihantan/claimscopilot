"""Guard against the LangGraph regression where a graph node name collides
with an AgentState key. LangGraph 0.2+ raises at graph compile time
(`'<name>' is already being used as a state key`), but it's cheaper to
fail in CI before bundle deploy than to discover it at app startup."""

from __future__ import annotations

from backend.agent.agent import AgentState, _build_graph


def _node_names(compiled) -> set[str]:
    # The compiled CompiledGraph exposes its graph's nodes via .nodes dict.
    return set(compiled.get_graph().nodes.keys())


def test_graph_compiles_and_no_node_matches_a_state_key():
    compiled = _build_graph()
    state_keys = set(AgentState.__annotations__.keys())
    node_names = _node_names(compiled)
    collisions = state_keys & node_names
    assert not collisions, f"node names collide with state keys: {collisions}"


def test_graph_has_the_expected_nodes():
    compiled = _build_graph()
    nodes = _node_names(compiled)
    for required in {"load_claim", "vuln_gate", "plan_step", "execute_step", "reflect_step"}:
        assert required in nodes, f"missing node: {required}"
