# Shared TidyBot SDK migration

## Target

```text
application / benchmark
        -> shared TidyBotSDK
        -> environment-specific RobotBackend
        -> service client / transport
        -> independent service
```

RoboCasa, Robosuite, and real hardware are peer backends. They do not run in a
single service process.

## Completed on the AttentionBench branch

- Moved the SDK facade and contracts out of `benchmarks/attention_harness` into
  the top-level `tidybot_sdk` package.
- Renamed the client-side contract from ambiguous `RobotService` to
  `RobotBackend`.
- Kept Robosuite's seven-dimensional OSC action inside
  `RobosuiteRobotBackend`.
- Kept the HTTP transport inside `RobosuiteSimClient` and the environment inside
  the independent `robosuite_sim` server.
- Added `ModuleRobotBackend` for existing agent_server module objects without
  importing agent_server or hardware dependencies.
- Made `find_objects()` an explicit optional capability. The RoboCasa module
  path can enable it; Robosuite cannot until it has a non-oracle perception
  service.
- Retained compatibility imports for the first AttentionHarness revision.

## Still required before declaring the platform fully unified

1. Add `tidybot_sdk` as a versioned dependency of the separate `agent_server`
   repository.
2. Instantiate `ModuleRobotBackend` from the existing `ArmAPI`, `SensorAPI`, and
   `GripperAPI` objects in agent_server's code-execution bootstrap.
3. Normalize the existing RoboCasa frame split: arm commands are arm-base frame
   while `find_objects()` currently returns world-frame positions.
4. Preserve optional modules (`base`, `wb`, `rewind`, `yolo`, `graspgen`) through
   capability protocols instead of making the minimal AttentionBench facade
   pretend every robot has them.
5. Run existing RoboCasa and hardware regression tests before replacing the old
   module initialization path.
6. Remove the AttentionHarness compatibility wrappers only after both runtime
   paths consume the shared package.

The current branch completes the shared core and the Robosuite consumer. It
does not silently modify the production agent_server checkout.
