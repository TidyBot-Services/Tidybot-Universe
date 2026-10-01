"""Policy-local public-input compiler, embedded verbatim in both v1 policies.

Only sandbox-supported builtins/methods are used. This file is source material,
not imported by the robot worker. It never issues robot commands.
"""


def locate(text, token):
    matches = []
    for index in range(len(text) - len(token) + 1):
        if text[index:index + len(token)] == token:
            matches += [index]
    return matches


def text_parameters(text, suite):
    limits = ({"grasp_offset_m": (0.0, 0.04),
               "approach_tolerance_m": (0.003, 0.020),
               "close_settle_steps": (10, 40)} if suite == "robosuite" else
              {"grasp_offset_m": (0.030, 0.050),
               "base_forward_m": (0.090, 0.125),
               "open_settle_steps": (10, 30),
               "close_settle_steps": (10, 40)})
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
        except Exception:
            return {}
    if (suite == "robosuite" and "lower it" in text
            and "grasp height" in text and "gripper" in text):
        return {"grasp_offset_m": 0.005, "approach_tolerance_m": 0.004,
                "close_settle_steps": 30}
    if (suite == "robocasa" and "verify approach and grasp success" in text
            and "get_observation" in text):
        return {"grasp_offset_m": 0.045, "open_settle_steps": 20,
                "close_settle_steps": 30}
    return {}


def read_public_json(text):
    # Deliberately bounded JSON subset: ordinary strings/escapes, maps, arrays
    # and finite numeric literals. Unsupported escapes cause baseline fallback.
    if not 1 <= len(text) <= 4000:
        raise RuntimeError("demo text bound")
    nodes = [0]

    def whitespace(index):
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    def value(index, depth):
        nodes[0] += 1
        if depth > 12 or nodes[0] > 256:
            raise RuntimeError("demo nesting bound")
        index = whitespace(index)
        if index >= len(text):
            raise RuntimeError("demo truncated")
        character = text[index]
        if character == '"':
            index += 1
            result = ""
            escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b",
                       "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
            while index < len(text):
                character = text[index]
                index += 1
                if character == '"':
                    return result, index
                if character == "\\":
                    if index >= len(text) or text[index] not in escapes:
                        raise RuntimeError("demo string escape")
                    character = escapes[text[index]]
                    index += 1
                elif character < " ":
                    raise RuntimeError("demo string control character")
                result += character
            raise RuntimeError("demo unterminated string")
        if character in "{[":
            is_map = character == "{"
            closing = "}" if is_map else "]"
            result = {} if is_map else []
            index = whitespace(index + 1)
            if index < len(text) and text[index] == closing:
                return result, index + 1
            while index < len(text):
                if is_map:
                    if text[index] != '"':
                        raise RuntimeError("demo map key")
                    key, index = value(index, depth + 1)
                    index = whitespace(index)
                    if key in result or index >= len(text) or text[index] != ":":
                        raise RuntimeError("demo duplicate key or colon")
                    item, index = value(index + 1, depth + 1)
                    result[key] = item
                else:
                    item, index = value(index, depth + 1)
                    result += [item]
                index = whitespace(index)
                if index >= len(text):
                    break
                if text[index] == closing:
                    return result, index + 1
                if text[index] != ",":
                    raise RuntimeError("demo separator")
                index = whitespace(index + 1)
            raise RuntimeError("demo unterminated container")
        for token, item in (("true", True), ("false", False), ("null", None)):
            if text[index:index + len(token)] == token:
                return item, index + len(token)
        start = index
        while index < len(text) and text[index] in "0123456789.+-eE":
            index += 1
        if start == index:
            raise RuntimeError("demo number")
        item = float(text[start:index])
        if not -10000 <= item <= 10000:
            raise RuntimeError("demo nonfinite/large number")
        return item, index

    result, index = value(0, 0)
    if whitespace(index) != len(text):
        raise RuntimeError("demo trailing data")
    return result


def bounded_number(arguments, key, low, high, integer=False, default=None):
    raw = arguments.get(key, default)
    if str(raw) in ("True", "False", "None"):
        raise RuntimeError("demo numeric type")
    number = float(raw)
    if not low <= number <= high or (integer and number != int(number)):
        raise RuntimeError("demo numeric bounds")
    return int(number) if integer else number


def demo_parameters(text, suite):
    try:
        projection = read_public_json(text)
        if (sorted(projection.keys()) != ["assets", "schema_version"]
                or projection["schema_version"] != "attentionbench.public-demo.v1"):
            return {}
        actions = None
        for asset in projection["assets"]:
            if asset.get("kind") == "action_trajectory":
                if actions is not None or not 1 <= len(asset["steps"]) <= 32:
                    return {}
                actions = asset["steps"]
        if actions is None:
            return {}
        if suite == "robosuite":
            operations = ["sdk.gripper.open", "sdk.arm.move_to_position",
                          "sdk.arm.move_to_position", "sdk.gripper.close",
                          "sdk.arm.move_to_position"]
            if len(actions) != 5:
                return {}
            for index in range(5):
                if (sorted(actions[index].keys()) != ["arguments", "operation"]
                        or actions[index]["operation"] != operations[index]):
                    return {}
            points = []
            for index in (1, 2, 4):
                arguments = actions[index]["arguments"]
                if any(key not in ("x", "y", "z", "tolerance", "max_steps")
                       for key in arguments):
                    return {}
                points += [(bounded_number(arguments, "x", -2, 2),
                            bounded_number(arguments, "y", -2, 2),
                            bounded_number(arguments, "z", 0, 2),
                            bounded_number(arguments, "tolerance", .003, .020,
                                           default=.004),
                            bounded_number(arguments, "max_steps", 40, 100,
                                           integer=True, default=100))]
            anchor = (points[0][0], points[0][1], points[0][2] - .12)
            relative = []
            for point in points:
                offset = (point[0] - anchor[0], point[1] - anchor[1],
                          point[2] - anchor[2], point[3], point[4])
                if abs(offset[0]) > .03 or abs(offset[1]) > .03:
                    return {}
                relative += [offset]
            if not (0 <= relative[1][2] <= .04 and .15 <= relative[2][2] <= .25):
                return {}
            if any(key != "settle_steps" for index in (0, 3)
                   for key in actions[index]["arguments"]):
                return {}
            return {"demo_points": relative,
                    "open_settle_steps": bounded_number(actions[0]["arguments"],
                       "settle_steps", 10, 30, integer=True, default=10),
                    "close_settle_steps": bounded_number(actions[3]["arguments"],
                       "settle_steps", 10, 40, integer=True, default=10)}
        # RoboCasa: base-route prefix, then the exact original wrist alignment.
        if not 3 <= len(actions) <= 10:
            return {}
        route = []
        for step in actions[:-2]:
            if (sorted(step.keys()) != ["arguments", "operation"]
                    or step["operation"] != "sdk.base.move_delta"):
                return {}
            arguments = step["arguments"]
            if (any(key not in ("dx", "dy", "dtheta", "frame") for key in arguments)
                    or arguments.get("frame") != "local"):
                return {}
            dx = bounded_number(arguments, "dx", 0, .15)
            dy = bounded_number(arguments, "dy", -.10, .10)
            if bounded_number(arguments, "dtheta", 0, 0, default=0) != 0:
                return {}
            route += [(dx, dy)]
        for step, rotation in zip(actions[-2:], ([-.175, .165, 0], [0, 0, -.80])):
            if (sorted(step.keys()) != ["arguments", "operation"]
                    or step["operation"] != "sdk.arm.move_delta"
                    or sorted(step["arguments"].keys()) !=
                         ["dx", "dy", "dz", "rotation_delta"]
                    or step["arguments"]["rotation_delta"] != rotation
                    or any(step["arguments"][key] != 0 for key in ("dx", "dy", "dz"))):
                return {}
        if not .20 <= sum(point[0] for point in route) <= .60:
            return {}
        return {"demo_base_route": route}
    except Exception:
        return {}


def compile_attention(attention, suite):
    parameters = demo_parameters(attention.get("demo_prior", ""), suite)
    receipt = {"demo": dict(parameters), "memory": {}, "advisor": {}}
    selected = attention.get("memory_ids_to_use", [])
    memory = attention.get("memory_guidance", {})
    for memory_id in sorted(selected):
        compiled = text_parameters(memory.get(memory_id, ""), suite)
        receipt["memory"][memory_id] = compiled
        for key, item in compiled.items():
            parameters[key] = item
    advisor = text_parameters(attention.get("advisor_guidance", ""), suite)
    receipt["advisor"] = advisor
    for key, item in advisor.items():
        parameters[key] = item
    # Compilation receipt is diagnostic only. Actual adoption is determined
    # externally from completed robot commands and backend actions.
    print("guidance_compile_v1", receipt)
    return parameters
