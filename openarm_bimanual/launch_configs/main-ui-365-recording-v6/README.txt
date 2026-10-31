Native human display 1920x1080, matching RoboCasa human_camera_service. Policy observations and approved physical configs remain 128x128 and byte-identical.
This independent service copies recording_service, adds only capture_human_view after existing reset/env.step, and declares its native human resolution. No extra physics step or SDK observation request.
StepRecorder preserves timestamps, identity and native dimensions; recording/live preview share actual render pixels. Static human evidence is extracted from the real HD captures. The original policy static PNGs remain under policy_camera_initial/final.png and observation JSONs remain unchanged.
No old video or approved package is upscaled, rewritten or backfilled.
Budget SHA remains 0bee25c33b6b13c37a819094b2255345a65a77b5a72a468b2aa253c63d8b4c3e; maximum attempts10 and assistance credits9.
