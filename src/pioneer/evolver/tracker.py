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


class CoverageTracker:
    def __init__(self, world: dict, max_depth: int = 4):
        self.max_depth = max_depth
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
        """[INDEPENDENT] n (parent, stressor) assignments for one generation.

        Stressor: refusal outcomes the pool lacks force policy_pushback first, then the
        least-covered stressor. Parent: from the shallowest uncovered depth's parents
        when there is one, otherwise any; the one with the fewest children first.
        """
        rng = random.Random(f"{seed}:{len(self.pool)}")
        eligible = [r for r in self.pool if r["meta"]["depth"] < self.max_depth]
        if not eligible:
            return []
        missing_refusals = [o for o in sorted(REFUSAL_OUTCOMES) if not self.counts["outcome"][o]]
        target_depth = next((d for d in range(1, self.max_depth + 1) if not self.counts["depth"][str(d)]), None)
        shallow = [r for r in eligible if target_depth and r["meta"]["depth"] == target_depth - 1]
        planned_stressors, planned_children, plan = Counter(), Counter(), []
        for slot in range(n):
            if slot < len(missing_refusals):
                stressor = Stressor.POLICY_PUSHBACK
            else:
                stressor = min(Stressor, key=lambda s: (self.counts["stressor"][s.value] + planned_stressors[s.value],
                                                        rng.random()))
            parent = min(shallow or eligible, key=lambda r: (
                self.children[r["task"]["id"]] + planned_children[r["task"]["id"]], rng.random()))
            planned_stressors[stressor.value] += 1
            planned_children[parent["task"]["id"]] += 1
            plan.append(dict(slot=slot, parent_id=parent["task"]["id"], stressor=stressor.value))
        return plan
