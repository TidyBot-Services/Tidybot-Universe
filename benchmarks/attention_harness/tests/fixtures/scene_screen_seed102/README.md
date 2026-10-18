# Seed 102 scene-screen geometry fixture

`reset.json` and `ik.json` contain only the fields read by
`test_seed102_recorded_geometry_matches_successful_command_scale`, copied from
`outputs/scene-screen-control/{reset,ik}.json` in the `wt-screen` worktree.
The original files are run outputs and are not versioned. All recorded numeric
values are preserved without rounding.

At extraction time, `ik.json` was in the old format and lacked the selected
orientation and derived geometry fields. The fixture adds
`grasp_quaternion_world_xyzw = [sin(17.5°), cos(17.5°), 0, 0]` **manually**
for the previously compared 55° successful pose, plus
`grasp_theta_deg = 55` and `selected_candidate_number = 3` from that comparison.
`target_z_range_world_m` is copied from the recorded reset row;
`object_height_m` is its exact upper-minus-lower difference. These additions
are only for a numerical scale/sign comparison. They are not a newly recorded
IK result or evidence that the selected pose was returned by the old screen.
