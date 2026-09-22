"""Deterministic fake agent. No LLM, no network."""

from aeris.simulation.agent import InjectedFault, SimulatedAgent
from aeris.simulation.scenarios import Scenario, builtin_scenarios, get_scenario

__all__ = ["InjectedFault", "Scenario", "SimulatedAgent", "builtin_scenarios", "get_scenario"]
