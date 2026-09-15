---
name: demo-skill
description: "Fixture skill used to exercise validate_pack() in tests/test_skill_packs.py. Not a real, registered skill."
---

# Demo Skill

This is a fixture-only skill. It exists so `tests/test_skill_packs.py` has a
known-valid `references/pack.yaml` to validate, independent of whatever real
packs (e.g. `configure-datadog-workspace`) land in `skills/` over time.
