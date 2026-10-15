"""Read-only comparison of V2 analytic Jacobians with central FK differences."""
import numpy as np


def inspect(model, state, *, epsilon):
    # V2 reuses output buffers. Copy every result before the next FK call.
    from curobo.types import JointState
    q = state.position.clone()
    result = model.compute_kinematics(state)
    analytic = result.tool_jacobians.detach().cpu().numpy().copy()[0,0]
    reference_p = result.tool_poses.position.detach().cpu().numpy().copy()[0,0]
    reference_q = result.tool_poses.quaternion.detach().cpu().numpy().copy()[0,0]
    numerical = np.zeros_like(analytic)

    def fk(position):
        r = model.compute_kinematics(JointState.from_position(position, joint_names=model.joint_names))
        return (r.tool_poses.position.detach().cpu().numpy().copy()[0,0],
                r.tool_poses.quaternion.detach().cpu().numpy().copy()[0,0])

    for j in range(q.shape[-1]):
        plus = q.clone(); plus[...,j] += epsilon
        minus = q.clone(); minus[...,j] -= epsilon
        pp, qp = fk(plus); pm, qm = fk(minus)
        qp *= np.where(np.sum(qp*reference_q,axis=-1,keepdims=True)<0,-1,1)
        qm *= np.where(np.sum(qm*reference_q,axis=-1,keepdims=True)<0,-1,1)
        # vector part of q_plus * conjugate(q_minus): world angular velocity.
        vector = -qp[:,:1]*qm[:,1:] + qm[:,:1]*qp[:,1:] - np.cross(qp[:,1:],qm[:,1:])
        numerical[:,0:3,j] = (pp-pm)/(2*epsilon)
        numerical[:,3:6,j] = vector/epsilon
    # Restore the unperturbed computational buffers; physical state is untouched.
    fk(q)
    absolute = abs(analytic-numerical)
    tolerance = .005 + .02*abs(numerical)
    return {"scope":"read_only_v2_kinematics_derivative_diagnosis", "physical_dispatches":0,
            "epsilon_rad":epsilon, "absolute_tolerance":.005, "relative_tolerance":.02,
            "joint_names":model.joint_names, "reference_position":reference_p.tolist(),
            "reference_quaternion_wxyz":reference_q.tolist(),
            "analytic":analytic.tolist(), "central_difference":numerical.tolist(),
            "max_absolute_error":float(absolute.max()),
            "within_tolerance":bool(np.all(absolute<=tolerance)),
            "singular_values":[np.linalg.svd(x,compute_uv=False).tolist() for x in analytic]}


def inspect_cost_gradient(seed_solver, state, goal, *, model, epsilon):
    """Compare the actual V2 seed IK pose cost backward with its finite difference."""
    from copy import deepcopy
    from curobo.types import JointState
    from curobo._src.cost.cost_tool_pose import ToolPoseCost
    import torch
    cost = ToolPoseCost(deepcopy(seed_solver.error_calculator.pose_cost.config))
    cost.setup_batch_tensors(1,1)
    q = state.position.detach().clone().requires_grad_(True)
    def evaluate(position):
        kin = model.compute_kinematics(JointState.from_position(position,joint_names=model.joint_names))
        return cost.forward(kin.tool_poses,goal,idxs_goal=torch.zeros((1,1),dtype=torch.int32,device=q.device))[0].sum()
    initial = evaluate(q)
    initial_value = float(initial.detach().cpu())
    initial.backward()
    analytic = q.grad.detach().cpu().numpy().copy()[0]
    numerical = np.zeros_like(analytic)
    for j in range(q.shape[-1]):
        plus = q.detach().clone(); plus[...,j] += epsilon
        minus = q.detach().clone(); minus[...,j] -= epsilon
        vp = float(evaluate(plus).detach().cpu())
        vm = float(evaluate(minus).detach().cpu())
        numerical[j] = (vp-vm)/(2*epsilon)
    evaluate(q.detach())
    return {'scope':'read_only_actual_v2_seed_pose_cost_gradient', 'physical_dispatches':0,
            'epsilon_rad':epsilon,'pose_cost':initial_value,
            'backward':analytic.tolist(),'central_difference':numerical.tolist(),
            'max_absolute_error':float(abs(analytic-numerical).max()),
            'within_tolerance':bool(np.all(abs(analytic-numerical)<=.005+.02*abs(numerical)))}
