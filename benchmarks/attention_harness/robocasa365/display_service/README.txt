Native human display 1920x1080, matching RoboCasa human_camera_service. Policy observations and approved physical configs remain 128x128 and byte-identical.
This independent service copies recording_service, adds only capture_human_view after existing reset/env.step, and declares its native human resolution. No extra physics step or SDK observation request.
StepRecorder preserves timestamps, identity and native dimensions; recording/live preview share actual render pixels. Static human evidence is extracted from the real HD captures. The original policy static PNGs remain under policy_camera_initial/final.png and observation JSONs remain unchanged.
No old video or approved package is upscaled, rewritten or backfilled.
