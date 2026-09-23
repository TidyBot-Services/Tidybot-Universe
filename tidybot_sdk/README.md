# Shared TidyBot SDK core

This package owns the environment-neutral SDK facade used by robot policies.
It does not import a simulator, hardware driver, service client, benchmark, or
evaluator.

```text
application / harness
        -> TidyBotSDK
        -> RobotBackend (client-side protocol)
        -> environment adapter
        -> service client / transport
        -> independent simulator or hardware service
```

`RobotBackend` is intentionally a client-side name. For the Robosuite path the
concrete stack is:

```text
TidyBotSDK
        -> RobosuiteRobotBackend
        -> RobosuiteSimClient
        -> HTTP
        -> robosuite_sim service
        -> official Robosuite
```

The contract uses high-level arm and gripper operations. Simulator-specific
action vectors remain inside the environment backend. This lets the existing
agent_server module objects use the same facade through `ModuleRobotBackend`:

```text
TidyBotSDK
        -> ModuleRobotBackend
        -> existing ArmAPI / SensorAPI / GripperAPI
        -> RoboCasa or hardware services
```

`PerceptionModuleRobotBackend` explicitly enables the optional
`sensors.find_objects()` capability. The plain module backend and the Robosuite
backend do not advertise it. This keeps capability differences visible rather
than silently exposing simulator oracle state.

Policy code still imports the runtime module as `robot_sdk`; each execution
host installs or injects the shared facade with the selected backend. Existing
RoboCasa and hardware SDK modules can be migrated behind the same facade
without putting those environments in one process.
