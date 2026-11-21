"""[BLOG] The global tracker.

"A global tracker watches what has already been covered and steers each
generation toward what hasn't, including outcomes where the correct behavior is
to say no."

[INDEPENDENT] The blog does not say which dimensions are tracked or how the gaps
steer generation. Here the tracker counts, over admitted tasks: write tools,
write-tool pairs, stressors, outcomes, depths and the world-model rules a task
touches. ``gaps()`` is handed to the Evolver agent; ``plan()`` decides which
(parent, stressor) pairs a generation works on. Pure program, no model calls.
"""
import random
from collections import Counter
from itertools import combinations_with_replacement

from pioneer.schemas import ExpectedOutcome, Stressor

REFUSAL_OUTCOMES = {ExpectedOutcome.REFUSE.value, ExpectedOutcome.PARTIAL.value}


def pair_key(a: str, b: str) -> str:
    return "+".join(sorted((a, b)))


DEPTH_TARGETS = (40, 200, 200, 120, 40)  # [INDEPENDENT] admitted tasks wanted per depth 0..4 (~600 per domain)
MAX_CHILDREN_PER_PARENT = 3  # per generation


class CoverageTracker:
    def __init__(self, world: dict, max_depth: int = 4, depth_targets=None,
                 max_children_per_parent: int = MAX_CHILDREN_PER_PARENT):
        self.max_depth = max_depth
        targets = list(depth_targets or DEPTH_TARGETS)
        self.depth_targets = (targets + targets[-1:] * max_depth)[:max_depth + 1]
        self.max_children_per_parent = max_children_per_parent
        self.writes = sorted(n for n, t in world["tools"].items() if t.get("tool_type") == "write")
        self.rule_tools = {r["rule_id"]: set(r["tools"]) for r in world["rules"]}
        self.universe = {
            "write_tool": self.writes,
            "tool_pair": sorted(pair_key(a, b) for a, b in combinations_with_replacement(self.writes, 2)),
            "stressor": [s.value for s in Stressor],
            "outcome": [o.value for o in ExpectedOutcome],
            "depth": [str(d) for d in range(self.max_depth + 1)],
            "rule": sorted(self.rule_tools),
        }
        self.counts = {dim: Counter() for dim in self.universe}
        self.write_counts = Counter()
        self.children = Counter()
        self.pool: list[dict] = []
        self._ids: set[str] = set()

    # -- observing admitted tasks ------------------------------------------------
    def add(self, row: dict) -> None:
        task_id = row["task"]["id"]
        if task_id in self._ids:
            return
        self._ids.add(task_id)
        self.pool.append(row)
        meta = row["meta"]
        names = [a["name"] for a in row["task"]["evaluation_criteria"].get("actions") or []]
        names += [r["tool"] for r in meta.get("refusal_requests") or []]
        writes = sorted(n for n in names if n in self.writes)
        for tool in set(writes):
            self.counts["write_tool"][tool] += 1
        for i, a in enumerate(writes):
            for b in writes[i + 1:]:
                self.counts["tool_pair"][pair_key(a, b)] += 1
        for stressor in {str(s) for s in meta.get("stressors", [])}:
            self.counts["stressor"][stressor] += 1
        self.counts["outcome"][str(meta["expected_outcome"])] += 1
        self.counts["depth"][str(meta["depth"])] += 1
        for rule, tools in self.rule_tools.items():
            if tools & set(names):
                self.counts["rule"][rule] += 1
        self.write_counts[meta.get("num_write_actions", len(writes))] += 1
        if meta.get("parent_id"):
            self.children[meta["parent_id"]] += 1

    def add_all(self, rows) -> "CoverageTracker":
        for row in rows:
            self.add(row)
        return self

    # -- reporting -----------------------------------------------------------------
    def gaps(self) -> dict[str, list[str]]:
        """Targets never seen, per dimension. Depth 0 is the starting point, not a gap."""
        return {dim: [t for t in targets if not self.counts[dim][t] and not (dim == "depth" and t == "0")]
                for dim, targets in self.universe.items()}

    def summary(self) -> dict:
        return dict(tasks=len(self.pool),
                    covered={dim: f"{sum(bool(self.counts[dim][t]) for t in targets)}/{len(targets)}"
                             for dim, targets in self.universe.items()},
                    counts={dim: dict(self.counts[dim]) for dim in self.counts},
                    write_actions=dict(sorted(self.write_counts.items())), gaps=self.gaps())

    # -- steering ------------------------------------------------------------------
    def plan(self, n: int, seed) -> list[dict]:
        """[INDEPENDENT] Up to n (parent, stressor) assignments for one generation.

        Layers fill in order: each slot grows the shallowest depth still short of its
        target (counting children already planned), from parents one level down. A parent
        gets at most ``max_children_per_parent`` slots per generation, the one with the
        fewest children first; when a layer's parents are used up the next layer is tried.
        (Rushing the first empty depth put a whole generation on its single parent.) Once
        every target is met, any parent below the maximum depth is used. Stressor:
        refusal outcomes the pool lacks force policy_pushback first, then the
        least-covered stressor. Fewer than n slots come back when every parent is capped.
        """
        rng = random.Random(f"{seed}:{len(self.pool)}")
        eligible = [r for r in self.pool if r["meta"]["depth"] < self.max_depth]
        missing_refusals = [o for o in sorted(REFUSAL_OUTCOMES) if not self.counts["outcome"][o]]
        planned_stressors, planned_children, planned_depth, plan = Counter(), Counter(), Counter(), []

        def open_parents(depth=None):
            return [r for r in eligible if (depth is None or r["meta"]["depth"] == depth)
                    and planned_children[r["task"]["id"]] < self.max_children_per_parent]

        for slot in range(n):
            short = [d for d in range(1, self.max_depth + 1)
                     if self.counts["depth"][str(d)] + planned_depth[d] < self.depth_targets[d]]
            candidates = next((c for c in (open_parents(d - 1) for d in short) if c), None) or open_parents()
            if not candidates:
                break
            parent = min(candidates, key=lambda r: (
                self.children[r["task"]["id"]] + planned_children[r["task"]["id"]], rng.random()))
            if len(plan) < len(missing_refusals):
                stressor = Stressor.POLICY_PUSHBACK
            else:
                stressor = min(Stressor, key=lambda s: (self.counts["stressor"][s.value] + planned_stressors[s.value],
                                                        rng.random()))
            planned_stressors[stressor.value] += 1
            planned_children[parent["task"]["id"]] += 1
            planned_depth[parent["meta"]["depth"] + 1] += 1
            plan.append(dict(slot=slot, parent_id=parent["task"]["id"], stressor=stressor.value))
        return plan
