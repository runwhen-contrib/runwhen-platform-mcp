#!/usr/bin/env bash
# Acceptance checks for the runwhen-agent-builder skill itself.
#
# Run from the repo root:   bash skills/runwhen-agent-builder/scripts/check.sh
#
# These verify the skill is well-formed and internally consistent. They do NOT
# verify a build you produced with it — for that, see the definition of done in
# SKILL.md and the eval protocol in references/eval-protocol.md.
set -u
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fails=0
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fails=$((fails+1)); }
has()  { grep -qF "$2" "$1"; }

echo "runwhen-agent-builder acceptance checks"
echo

# --- SKILL.md ---
F="$D/SKILL.md"
if [ ! -f "$F" ]; then bad "SKILL.md missing"; else
  head -1 "$F" | grep -qx -- '---' || bad "SKILL.md has no frontmatter"
  grep -qx 'name: runwhen-agent-builder' "$F" || bad "frontmatter name must match the directory"
  grep -q '^description: "' "$F" || bad 'description must be double-quoted (it contains "Use when: ")'
  # Budget raised from 200 to 300 when this skill merged with the probe-first
  # / report-contract / ledger material. Deliberate, not drift: the phases are
  # the substance and cramming them into references is how they get skipped.
  L=$(wc -l < "$F" | tr -d ' ')
  [ "$L" -le 300 ] && ok "SKILL.md $L lines (<=300)" || bad "SKILL.md $L lines exceeds the 300-line budget"
  for phase in 0 1 2 3 4 5 6 7; do
    has "$F" "Phase $phase" || bad "SKILL.md missing Phase $phase"
  done
  has "$F" "search_registry"       || bad "SKILL.md must mandate registry-first reuse"
  has "$F" "merged secret inventory" || bad "SKILL.md must require the merged secret inventory"
  has "$F" "Re-gate"               || bad "SKILL.md must require re-gating on material change"
  has "$F" "context sources"       || bad "SKILL.md must require asking for context sources"
  has "$F" "retention ladder"      || bad "SKILL.md must require probing the retention ladder"
  has "$F" "schedule_paused"       || bad "SKILL.md must create scheduled commands paused"
  has "$F" "run_metadata"          || bad "SKILL.md must require the run_metadata block"
  grep -qE 'Skill\(|runwhen-skill://' "$F" && bad "SKILL.md must stay self-contained (no skill calls out)"
  for r in $(grep -oE 'references/[a-z-]+\.md' "$F" | sort -u); do
    [ -f "$D/$r" ] || bad "dangling reference $r"
  done
fi

# --- references ---
declare -a REQ=(
  "object-model.md|## commit_slx|## Variable families|## Assistant|## Rules vs commands|## Scheduled command|## Knowledge base|## Queries that lie|## Debugging a FAILED run|## Handoff mechanics"
  "task-contract.md|## Breadth task|## Depth task|## The name-carrying report|## Determinism rule|## Build economy|## Retrieval check"
  "decomposition.md|## The five filters|## Plan output format"
  "investigation-surface.md|## Method|## The layer trap"
  "eval-protocol.md|## Writing the questions|## Pass bar"
  "build-ledger.md|## Teardown order|## state.md|## Resuming"
  "gap-report-template.md|## Template"
  "report-contract.md|## The top of the report is fixed|## Write forward, not backward|## Delivery is invisible"
)
for spec in "${REQ[@]}"; do
  IFS='|' read -r file rest <<< "$spec"
  P="$D/references/$file"
  if [ ! -f "$P" ]; then bad "references/$file missing"; continue; fi
  miss=0
  IFS='|' read -ra secs <<< "$rest"
  for sec in "${secs[@]}"; do has "$P" "$sec" || { bad "references/$file missing section: $sec"; miss=1; }; done
  [ "$miss" -eq 0 ] && ok "references/$file"
done

# --- facts that must survive edits ---
O="$D/references/object-model.md"
if [ -f "$O" ]; then
  for fact in 'issue title' 'runtime_vars_provided' 'secretsProvided' 'auto_approve_readonly' 'max_runs' 'GEN_CMD' '_enforce_custom_resource_path'; do
    has "$O" "$fact" || bad "object-model.md lost the fact: $fact"
  done
fi
for fact in 'DETAIL_LEVEL' 'run_metadata' 'one resource path' 'Tripwire hygiene'; do
  has "$D/references/task-contract.md" "$fact" || bad "task-contract.md lost the fact: $fact"
done
for fact in 'visibility boundary' 'schedule_paused' 'Which identity'; do
  has "$O" "$fact" || bad "object-model.md lost the fact: $fact"
done
[ -f "$D/scripts/probe-template.py" ] || bad "scripts/probe-template.py missing"
if [ -f "$D/templates/handover.html" ]; then
  for sec in 'What it does' 'How to use it' 'What was built' 'What it cannot do'; do
    has "$D/templates/handover.html" "$sec" || bad "handover.html missing section: $sec"
  done
  ok "templates/handover.html"
else bad "templates/handover.html missing"; fi
has "$D/references/report-contract.md" "## Writing the command prompt" || bad "report-contract.md must carry the command-prompt recipe"
has "$D/references/report-contract.md" "banded by urgency" || bad "report-contract.md must band the body by urgency"
has "$D/references/decomposition.md" "## Offer the choices before you draft the plan" || bad "decomposition.md must require the choice gate"

# --- no stubs anywhere ---
# Strip backticked spans first: these files legitimately *document* the tokens
# TODO/FIXME as things to avoid in report prose, and a naive substring match
# flags that documentation. Only unquoted occurrences are real stubs.
stubs=""
while IFS= read -r f; do
  if sed 's/`[^`]*`//g' "$f" | grep -qE '\b(TBD|TODO|FIXME)\b'; then
    stubs="$stubs $(basename "$f")"
  fi
done < <(find "$D" -name '*.md' -type f)
if [ -n "$stubs" ]; then bad "stub markers found in:$stubs"; else ok "no stub markers"; fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL CHECKS PASSED"; exit 0; else echo "$fails CHECK(S) FAILED"; exit 1; fi
