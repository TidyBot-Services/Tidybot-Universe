from robot_sdk import sensors, arm, gripper, base

# Keep the public-text format, with explicit OpenArm capabilities and bounds.
def locate(text, token):
    matches = []
    for index in range(len(text) - len(token) + 1):
        if text[index:index + len(token)] == token:
            matches += [index]
    return matches


def text_parameters(text, suite):
    limits = {"grasp_offset_m": (0.0, 0.020)}
    marker = "AB_CONTROL_V1 "
    matches = locate(text, marker)
    if matches:
        if len(matches) != 1:
            return {}
        source = text[matches[0] + len(marker):]
        result = {}
        try:
            index = 0
            while index < len(source):
                while index < len(source) and source[index] in " \t\r\n":
                    index += 1
                if index == len(source):
                    break
                start = index
                while index < len(source) and source[index] != "=":
                    index += 1
                key = source[start:index]
                if key in ("base_forward_m", "open_settle_steps", "close_settle_steps", "approach_tolerance_m"):
                    raise RuntimeError("OpenArm guidance does not support these controls: " + key)
                if key not in limits or key in result or index == len(source):
                    return {}
                index += 1
                start = index
                while index < len(source) and source[index] != ";":
                    if source[index] not in "0123456789.+-eE":
                        return {}
                    index += 1
                if index == len(source):
                    return {}
                value = float(source[start:index])
                index += 1
                low, high = limits[key]
                if not low <= value <= high:
                    return {}
                if key in ("open_settle_steps", "close_settle_steps"):
                    if value != int(value):
                        return {}
                    value = int(value)
                result[key] = value
            return result
        except RuntimeError:
            raise
        except Exception:
            return {}
    return {}



if context.get("robot", {}).get("model") != "OpenArm" or context["task_id"] != "counter_to_sink":
    raise RuntimeError("OpenArm counter_to_sink policy identity mismatch")
parameters = text_parameters(context.get("attention_input", {}).get("advisor_guidance", ""), "robocasa")
unsupported_controls = [key for key in parameters if key != "grasp_offset_m"]
if unsupported_controls:
    raise RuntimeError("OpenArm guidance does not support these controls")
print("guidance_compile_v1", {"demo": {}, "memory": {}, "advisor": parameters})
grasp_offset = parameters.get("grasp_offset_m", .005)

def target():
    found = sensors.find_objects(["obj"])
    if len(found) != 1:
        raise RuntimeError("Expected exactly one native task target")
    return found[0]["position"]

def move(x,y,z):
    # The backend plans and collision-checks every move before dispatch.
    # Avoid a duplicate plan/RGB-D query cycle in the 300-second task budget.
    arm.move_to_position(x,y,z,tolerance=.004,max_steps=100)

base.move_delta(0.03, 0, 0)
base.move_delta(0, 0, 0.04)
base.move_delta(0, 0, -0.04)
initial = target()
gripper.open(settle_steps=80)
move(initial[0],initial[1],initial[2]+.15)
for step in range(1,10):
    dz = .15 + (grasp_offset-.15)*step/9
    move(initial[0],initial[1],initial[2]+dz)
gripper.close(settle_steps=80)
for step in range(1,9):
    dz = grasp_offset + (.13-grasp_offset)*step/8
    move(initial[0],initial[1],initial[2]+dz)
lifted = target()
if lifted[2] < initial[2]+.07:
    raise RuntimeError("Observed target did not follow physical lift; grasp slipped")
move(3.9,-1.6,1.10)
# Shorter real transfers limit contact disturbances; all original tolerances remain.
for step in range(1,5):
    move(3.9+.005*step,-1.6+.07*step,1.10-.005*step)
gripper.open(settle_steps=80)
sensors.get_observation()
print("OpenArm physical policy finished; native evaluator verdict is owned by the runner")
