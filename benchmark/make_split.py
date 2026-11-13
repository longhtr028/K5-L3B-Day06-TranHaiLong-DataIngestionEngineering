"""Assign dev/test split once, before any retrieval run.

Rule: questions sharing a gold article form one group and land in the same split.
Groups are shuffled with a fixed seed, stratified by whether the question needs a filter:
2 filter groups go to test, then non-filter groups go to test until it holds >= 5 non-filter questions.
"""
import json
import random
from collections import defaultdict
from pathlib import Path

SEED = 20261113
PATH = Path(__file__).resolve().parent / "questions.jsonl"

questions = [json.loads(line) for line in PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
groups: dict[str, list[dict]] = defaultdict(list)
for q in questions:
    groups[q["group"]].append(q)

rng = random.Random(SEED)
filter_groups = sorted(g for g, qs in groups.items() if any(q["filter"] for q in qs))
plain_groups = sorted(g for g in groups if g not in filter_groups)
rng.shuffle(filter_groups)
rng.shuffle(plain_groups)

test = set(filter_groups[:2])
n_plain = 0
for g in plain_groups:
    if n_plain >= 5:
        break
    test.add(g)
    n_plain += len(groups[g])

for q in questions:
    q["split"] = "test" if q["group"] in test else "dev"
PATH.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions), encoding="utf-8")

for split in ("dev", "test"):
    qs = [q for q in questions if q["split"] == split]
    print(split, len(qs), "filter:", sum(bool(q["filter"]) for q in qs), [q["id"] for q in qs])
