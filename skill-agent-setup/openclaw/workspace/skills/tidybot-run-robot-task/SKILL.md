---
name: tidybot-run-robot-task
description: Execute a physical task on the robot (pick up, place, move, look at, etc.). Use when the user asks you to do something with/on the robot — any manipulation, navigation, or perception task. Triggers on requests like "pick up the banana," "put that on the plate," "go to the table," "what do you see," etc.
---

# Run Robot Task

When the user asks you to do something on the robot, follow this order strictly. Do not skip to writing raw SDK code.

## Step 1: Check Existing Skills

1. Check `dev/` folder for local in-progress skills that match the task
2. Fetch the skill catalog: `GET http://<ROBOT_IP>:8080/skills/catalog` (or check your memory for known skills)
3. If a matching skill exists, **use it** — do not reinvent

## Step 2: Chain If Possible

If no single skill matches but multiple existing skills can be combined:
1. Break the task into sub-steps mapped to existing skills
2. Execute them in sequence, passing context between steps
3. Chaining tested skills beats untested new code

## Step 3: Build New Only as Last Resort

If no existing skill covers the task:
1. Read the SDK guide first: `GET http://<ROBOT_IP>:8080/docs/guide/html`
2. Check the `active-services` skill for available backends
3. Follow the `tidybot-skill-dev` skill to build, test, and save to `dev/` (use `tb-` prefix for robot skills)

## Execution Checklist

Before running any code on the robot:
- [ ] Acquire a lease (`POST /lease/acquire`)
- [ ] Read the SDK guide if writing new code (every session, no exceptions)
- [ ] Use `print()` for status — poll `/code/status` for output
- [ ] Check recorded frames after execution to verify results
- [ ] Release lease or let it expire when done

## Key References

- Robot connection: see `robot-connection` skill
- Hardware specs: see `robot-hardware` skill
- SDK methods: see `robot-sdk-ref` skill
- Building new skills: see `tidybot-skill-dev` skill
- Available services: see `active-services` skill
