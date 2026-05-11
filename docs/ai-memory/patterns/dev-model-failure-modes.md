# Pattern — Dev-Agent Failure Modes Observed Across Models

**Observed across multiple sessions** (2026-04 to 2026-05). Patterns confirmed at least twice each before being recorded here.

## TL;DR

When a dev agent (the one writing skill code) fails, it usually isn't because the prompt lacks information. The same prompts work for stronger models. The failures cluster into a few **reasoning** patterns that are model-capability-specific.

## Failure modes

### 1. Silent-success misread

**Trigger:** dev's own code lacks a `print()` after a side-effect call (e.g. `wb.move_to_pose(...)` with no subsequent log).

**What happens:** the call completes cleanly (`exit_code=0`), but stdout stops at the last `print` before the side-effect. Dev sees "no output after my last log" and concludes "the call hung / failed / scene reset" — and pivots away from the correct tool.

**Concrete instance:** 2026-05-07 sink-to-counter session. DS-V4-Flash dev wrote `print("Trying wb to plate..."); wb.move_to_pose(...)`. wb call likely succeeded but no follow-up print. Dev decided "scene was reset" and went back to `arm.move_to_pose` for 30+ iterations — none of which could ever succeed (target was 1.18m from arm base, max reach 0.85m).

**Root cause:** weak working memory / weak prior on "silent success vs silent failure". Stronger models tend to wrap side-effect calls in `try/except` or print the return value, ensuring an unambiguous signal.

**Diagnosis:** when dev fails repeatedly, check the exec stdout for `exit_code=0` + abrupt end. If the script clearly didn't `print` after its last operation, this pattern.

### 2. Heuristic-revert after one tool failure

**Trigger:** prompt says "Always use graspgen first, then fall back to TopDown if it fails." Dev's first iteration tries graspgen, doesn't grasp on the first 3-5 candidates.

**What happens:** subsequent dev iterations completely restructure the code — `TopDown` heuristic moves to line 1, graspgen demoted to "backup" or removed entirely.

**Observation count:** 2+ sessions. Persistent across re-spawns and explicit prompt warnings.

**Root cause:** the dev's empirical observation of "graspgen didn't work" outweighs the prompt's instruction in next-iter planning. Classic "empirical win > prompt instruction" pattern.

**Diagnosis:** check exec stdout — if dev imported graspgen but never called it (no graspgen API trace), this pattern. Don't blame graspgen.

### 3. Reach-sweep instead of moving the base

**Trigger:** target object is outside arm reach (>0.85m from arm base).

**What happens:** dev iterates `arm.move_to_pose(x, y, z, ...)` with various `x` values, trying to reach an unreachable target. Doesn't recognize that `wb.move_to_pose(mask="whole_body")` would move the base AND the arm.

**Concrete instance:** 2026-05-07 sink-to-counter session. dev wrote `for x in [0, 0.1, 0.2, 0.3, 0.4]: arm.move_to_pose(x, 1.183, 0.4)`. All hit IK clamp because `y=1.183` alone is unreachable. Failure mode #2 had earlier kicked dev off graspgen + wb, leaving only `arm.move_to_pose` in its toolkit.

**Root cause:** confusion about which API moves what. dev knew `wb.move_to_pose` existed (sometimes tried it once) but didn't internalize "use wb when target > arm reach".

**Diagnosis:** compute distance from initial `arm_base_world` to target object. If >0.85m and dev keeps calling `arm.move_to_pose`, this pattern.

## What stronger models do

Empirically (claude-sonnet-4-6, claude-opus-4-7, on the same prompts):
- Wrap side-effect calls so the outcome is observable (`print(result)`, try/except)
- Backtrack to alternative tools when current path saturates, but only after **multiple distinct attempts** with current tool — not after one failure
- Reason about kinematic constraints from prompt-stated facts ("arm reach max 0.85m") and adjust **before** trying unreachable configurations

## How to mitigate (system-side)

These are model failures, but the system can soften them:

1. **Prompt-side**: require dev to add a "post-call assertion print" after every side-effect call. Reduces silent-success misread. Already partially done in current dev prompt.

2. **Evaluator-side**: if evaluator can detect "dev tried correct tool once then abandoned it", inject a hint reminding dev to re-try the abandoned tool. Not implemented.

3. **System-side**: if dev's first 3 iterations on a skill have the same structural error (e.g. all using arm.move_to_pose for unreachable target), break the autonomous retry loop and surface to human. Not implemented.

4. **Selection**: switch to a stronger model when failure rate exceeds threshold. The clean experiment proposed multiple times: same DAG, swap dev from DS-V4-Flash to Claude Opus 4.7, compare success rate.

## Related

- `~/.claude/projects/.../memory/feedback_dev_silent_success_misread.md` — full case study with stdout quotes
- `~/.claude/projects/.../memory/feedback_dev_agent_reverts_to_heuristic.md` — full case study
- `~/.claude/projects/.../memory/feedback_prompt_examples_get_copied.md` — related: dev copies code examples from prompt verbatim, which is a way to influence (but not cure) these patterns
