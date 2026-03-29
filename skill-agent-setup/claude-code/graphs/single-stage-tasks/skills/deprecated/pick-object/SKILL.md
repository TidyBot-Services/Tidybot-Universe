# pick-object

Detect a named target object on the counter, navigate to it, and grasp it.

## Pipeline

1. **Detect** — `sensors.find_objects()` (broad + targeted pass, merged)
2. **Locate** — exact-then-substring match for `target_name`
3. **Validate** — confirm object is at counter height (world z: 0.65–1.15 m)
4. **Grasp** — open gripper → approach from above → lower → close → check width
5. **Retry** — up to 7 attempts with z-offsets if grasp misses
6. **Lift** — raise 0.20 m after confirmed grasp

## Usage

```bash
python main.py [target_name]   # default: "obj"
```

## Output (machine-readable)

```
Detections: [{"name": "...", "position": [x, y, z]}, ...]
Target '<name>' at (x.xxx, y.yyy, z.zzz)
gripper width after close: X.XXXX m
Result: SUCCESS  |  Result: FAILED – <reason>
```

## Config

| Constant | Default | Description |
|---|---|---|
| `COUNTER_Z_MIN/MAX` | 0.65–1.15 m | Valid counter height band (world frame) |
| `APPROACH_CLEARANCE` | 0.15 m | Height above grasp point for approach |
| `LIFT_HEIGHT` | 0.20 m | Rise after successful grasp |
| `GRASP_Z_OFFSETS` | 7 values | Z retry offsets (biased slightly above centroid) |
| `GRIPPER_MIN/MAX_M` | 0.005–0.083 m | Width range confirming object in hand |

## Dependencies

- `robot_sdk`: `gripper`, `sensors`, `wb`
