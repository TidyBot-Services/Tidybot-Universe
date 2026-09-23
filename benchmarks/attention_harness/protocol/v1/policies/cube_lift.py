"""Frozen non-oracle RGB-D policy for cube_lift."""

from robot_sdk import arm, gripper, sensors


def locate_red_cube():
    observation = sensors.get_observation()
    image = observation["agentview_image"].tolist()
    depth = observation["agentview_depth"].tolist()
    intrinsics = observation["agentview_intrinsics"].tolist()
    pose = observation["agentview_pose_mat"].tolist()
    points = []
    for v, row in enumerate(image):
        for u, pixel in enumerate(row):
            red, green, blue = pixel
            if red > 80 and red > 1.4 * green and red > 1.4 * blue:
                z_camera = depth[v][u][0]
                x_camera = (u - intrinsics[0][2]) * z_camera / intrinsics[0][0]
                y_camera = (v - intrinsics[1][2]) * z_camera / intrinsics[1][1]
                x_world = pose[0][0] * x_camera + pose[0][1] * y_camera + pose[0][2] * z_camera + pose[0][3]
                y_world = pose[1][0] * x_camera + pose[1][1] * y_camera + pose[1][2] * z_camera + pose[1][3]
                z_world = pose[2][0] * x_camera + pose[2][1] * y_camera + pose[2][2] * z_camera + pose[2][3]
                points = points + [(x_world, y_world, z_world)]
    if len(points) < 10:
        raise RuntimeError("red cube was not visible in the public RGB image")
    median_z = sorted(point[2] for point in points)[len(points) // 2]
    top = [point for point in points if abs(point[2] - median_z) < 0.003]
    middle = len(top) // 2
    return (
        sorted(point[0] for point in top)[middle],
        sorted(point[1] for point in top)[middle],
        sorted(point[2] for point in top)[middle],
    )


cube_x, cube_y, cube_surface_z = locate_red_cube()
gripper.open(settle_steps=5)
arm.move_to_position(cube_x, cube_y, cube_surface_z + 0.12, max_steps=100)
arm.move_to_position(cube_x, cube_y, cube_surface_z - 0.015, max_steps=100)
gripper.close(settle_steps=30)
arm.move_to_position(cube_x, cube_y, 1.08, max_steps=100)
