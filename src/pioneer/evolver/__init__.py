"""Stage 2 - Evolver + global coverage tracker (PLAN Step 5).

[BLOG] Start from every atomic action; each generation applies evolution
stressors to existing scenarios to produce harder descendants. A global tracker
steers each generation toward uncovered outcomes, including refusals.
"""

from .agent import EvolverAgent, validate_scenario
from .pool import load_pool, parent_scenario
from .tracker import CoverageTracker

__all__ = ["CoverageTracker", "EvolverAgent", "load_pool", "parent_scenario", "validate_scenario"]
