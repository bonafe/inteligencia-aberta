from typing import TypedDict
from langgraph.graph import StateGraph, END
from agents.planejador import planejar
from agents.coletor import coletar
from agents.redator import redigir


class InvestigationState(TypedDict):
    query: str
    tenant_id: str
    classification: str
    plan: dict
    collected_data: list
    report: str


def build_graph():
    graph = StateGraph(InvestigationState)

    graph.add_node("planejador", planejar)
    graph.add_node("coletor", coletar)
    graph.add_node("redator", redigir)

    graph.set_entry_point("planejador")
    graph.add_edge("planejador", "coletor")
    graph.add_edge("coletor", "redator")
    graph.add_edge("redator", END)

    return graph.compile()


investigation_graph = build_graph()
