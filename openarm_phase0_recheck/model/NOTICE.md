# OpenArm model files: source and license

- `openarm_description/assets/...`: unmodified mesh files from
  [enactic/openarm_description](https://github.com/enactic/openarm_description) at commit
  `c10304158f917579491b02682e02f903b948d74d`, Apache License 2.0 (`LICENSE.txt` in this folder).
  Only the files used by the AttentionBench OpenArm365 model are included.
- `compat_v6/`: files derived from those meshes and the official Xacro output for AttentionBench
  (convex-part collision meshes, visual meshes with baked axis signs, URDF/SRDF). They were
  modified by the AttentionBench authors; `compat_v6/implementation_lock.json` records the
  changes (`mesh_repair_diff`, `visual_sign_baking`, disabled collision pairs). Distributed under
  the same Apache License 2.0.
- `generation_info.txt`: how the official URDF was generated.
