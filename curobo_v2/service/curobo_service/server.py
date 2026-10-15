"""FastAPI app exposing the planner over HTTP.

Endpoints
---------
GET  /health                  — readiness + warmup state
POST /warmup                  — explicit warmup trigger (idempotent)
POST /world/cuboids           — push collision cuboids for an env_id
GET  /world/envs              — list known env_ids
POST /plan                    — plan to EE pose
POST /plan/joint              — plan to joint config
POST /plan/ik                 — IK solve

env_id is passed via JSON body (default = CUROBO_DEFAULT_ENV from config).
"""

from typing import Optional, Literal

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
import math

from .config import Config
from .planner import PlannerAdapter
from .world import WorldStore


# ---- request models -------------------------------------------------------

class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True,allow_inf_nan=False)


class SingleAttachmentRequest(StrictRequest):
    holder: Literal['left','right']
    target_id: int = Field(ge=0)
    object_pose: list[list[float]]
    tcp_object_transform: list[list[float]]
    shape_names: list[str] = Field(min_length=1)
    support_names: list[str]
    tool_frame: Literal['openarm_left_hand_tcp','openarm_right_hand_tcp']

    @field_validator('object_pose','tcp_object_transform')
    @classmethod
    def measured_transform(cls,value):
        from curobo_v2.attachment import rigid_pose
        rigid_pose(value)
        return value


class CooperativeAttachmentRequest(StrictRequest):
    object_name: str = Field(min_length=1)
    object_pose: list[list[float]]
    tcp_object_transforms: dict[str,list[list[float]]]
    tool_frames: dict[str,str]
    support_names: list[str]

    @field_validator('object_pose')
    @classmethod
    def measured_transform(cls,value):
        from curobo_v2.attachment import rigid_pose
        rigid_pose(value)
        return value

    @field_validator('tcp_object_transforms')
    @classmethod
    def measured_transforms(cls,value):
        if set(value)!={'left','right'}:raise ValueError('Exactly two measured grasp transforms are required')
        from curobo_v2.attachment import rigid_pose
        for transform in value.values():rigid_pose(transform)
        return value

def unit_quaternion(value):
    if value is not None and (len(value)!=4 or abs(math.sqrt(sum(v*v for v in value))-1.)>1e-5):
        raise ValueError("Quaternion must be a finite unit wxyz vector")
    return value


class CuboidRequest(StrictRequest):
    name: str = Field(min_length=1)
    center: list[float] = Field(min_length=3,max_length=3)
    half_size: list[float] = Field(min_length=3,max_length=3)
    quat_wxyz: list[float] = Field(default_factory=lambda:[1.,0.,0.,0.],min_length=4,max_length=4)

class TargetRequest(StrictRequest):
    position: list[float] = Field(min_length=3,max_length=3)
    quaternion_wxyz: Optional[list[float]] = Field(None,min_length=4,max_length=4)

    @field_validator('quaternion_wxyz')
    @classmethod
    def valid_quaternion(cls,value):
        return unit_quaternion(value)

class WorldRequest(StrictRequest):
    env_id: Optional[str] = None
    cuboids: list[CuboidRequest] = Field(..., max_length=4096)
    robot_pos: Optional[list[float]] = Field(
        None, description="[x, y] of base in world frame; needed for arm-only mode")


class JointOptions(StrictRequest):
    frame: Optional[str] = None
    length_unit: str = Field("m",pattern="^m$")
    angular_unit: str = Field("rad",pattern="^rad$")

    @model_validator(mode="after")
    def check_frame(self):
        import yaml
        kin=yaml.safe_load(open(Config.ROBOT_CFG))["robot_cfg"]["kinematics"]
        if self.frame is not None and self.frame != kin["base_link"]:
            raise ValueError("Targets must be expressed in the declared robot base frame: "+kin["base_link"])
        return self

    joint_names: Optional[list[str]] = None
    active_joints: Optional[list[str]] = None
    locked_joints: Optional[dict[str, float]] = None


class PlanRequest(JointOptions):
    attachment: Optional[SingleAttachmentRequest | CooperativeAttachmentRequest] = None
    env_id: Optional[str] = None
    current_q: list[float]
    target_pose: Optional[list[float]] = None
    targets: Optional[dict[str, TargetRequest]] = None
    target_quat: Optional[list[float]] = Field(
        None, description="[w, x, y, z]; defaults to top-down")
    mask: str = Field("whole_body", pattern="^(whole_body|arm_only)$")

    @field_validator('target_quat')
    @classmethod
    def valid_quaternion(cls,value):
        return unit_quaternion(value)

    @model_validator(mode='after')
    def required_target(self):
        if self.targets is not None:
            if not self.targets or self.target_pose is not None or self.target_quat is not None:
                raise ValueError('Named targets must be nonempty and cannot be mixed with target_pose/target_quat')
        elif self.target_pose is None or len(self.target_pose)!=3:
            raise ValueError('Exactly three target position values are required')
        return self


class JointPlanRequest(JointOptions):
    attachment: Optional[SingleAttachmentRequest | CooperativeAttachmentRequest] = None
    env_id: Optional[str] = None
    current_q: list[float]
    target_qpos: list[float]


class FKRequest(JointOptions):
    env_id: Optional[str] = None
    current_q: list[float]


class JacobianRequest(FKRequest):
    tool_frames: list[str]
    epsilon: float = Field(.0001,gt=0.,le=.001)
    targets: Optional[dict[str, TargetRequest]] = None


class WarmupRequest(JointOptions):
    current_q: Optional[list[float]] = None
    tool_frames: Optional[list[str]] = None
    env_id: Optional[str] = None
    attachment: Optional[SingleAttachmentRequest | CooperativeAttachmentRequest] = None

    @model_validator(mode='after')
    def selectors_require_state(self):
        if self.current_q is None and any(getattr(self,key) is not None for key in
                ('joint_names','active_joints','locked_joints','tool_frames','env_id','attachment')):
            raise ValueError('Warmup selectors require the actual current_q; no selector may be ignored')
        return self


class CooperativeRequest(JointOptions):
    env_id: Optional[str] = None
    current_q: list[float]
    object_pose: list[list[float]]
    attachment: CooperativeAttachmentRequest


class IKRequest(JointOptions):
    env_id: Optional[str] = None
    target_pose: Optional[list[float]] = None
    targets: Optional[dict[str, TargetRequest]] = None
    target_quat: Optional[list[float]] = None
    mask: str = Field("whole_body", pattern="^(whole_body|arm_only)$")
    seed_q: list[float]

    @field_validator('target_quat')
    @classmethod
    def valid_quaternion(cls,value):
        return unit_quaternion(value)

    @model_validator(mode='after')
    def required_target(self):
        if self.targets is not None:
            if not self.targets or self.target_pose is not None or self.target_quat is not None:
                raise ValueError('Named targets must be nonempty and cannot be mixed with target_pose/target_quat')
        elif self.target_pose is None or len(self.target_pose)!=3:
            raise ValueError('Exactly three target position values are required')
        return self


class ValidateBaseRequest(StrictRequest):
    env_id: Optional[str] = None
    base_positions: list[list[float]]
    target_pos: list[float]
    base_box: dict = Field(..., description="{center_xy, half_extents}")


# ---- app factory ----------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="cuRobo Service",
        description="GPU-accelerated motion planning over HTTP",
        version="0.1.0",
    )

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse(status_code=422,content={"detail":str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # Nonfinite request values must not enter JSONResponse's error payload.
        return JSONResponse(status_code=422,content={'detail':[
            {k:e[k] for k in ('loc','msg','type') if k in e} for e in exc.errors()]})

    world = WorldStore(max_envs=Config.MAX_ENVS)
    planner = PlannerAdapter(device=Config.DEVICE, world_store=world)

    def _env(req_env: Optional[str]) -> str:
        return req_env or Config.DEFAULT_ENV

    # ------------------ health / warmup ------------------------------------

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "warmed_up": planner.is_warmed_up(),
            "device": Config.DEVICE,
            "robot_cfg": Config.ROBOT_CFG,
            "envs": world.list_envs(),
            "active_env": world.active(),
            "robot": planner._planner.identity,
        }

    @app.post("/warmup")
    def warmup(req: WarmupRequest):
        return planner.warmup(req.current_q, req.joint_names, req.active_joints,
                              req.locked_joints, req.tool_frames, req.env_id,
                              None if req.attachment is None else req.attachment.model_dump())

    # ------------------ world ----------------------------------------------

    @app.post("/world/cuboids")
    def push_world(req: WorldRequest):
        try:
            return planner.push_world(_env(req.env_id), [c.model_dump() for c in req.cuboids], req.robot_pos)
        except (ValueError,KeyError,TypeError) as e:
            raise HTTPException(status_code=422,detail=str(e))

    @app.get("/world/envs")
    def list_envs():
        return {"envs": world.list_envs(), "active": world.active()}

    # ------------------ plan -----------------------------------------------

    @app.post("/plan")
    def plan_pose(req: PlanRequest):
        try:
            if req.targets is not None and (req.target_pose is not None or req.target_quat is not None):
                raise ValueError("Specify targets or target_pose/target_quat, not both")
            return planner.plan_pose(
                env_id=_env(req.env_id),
                current_q=req.current_q,
                target_pos=req.target_pose,
                target_quat=req.target_quat,
                lock_base=(req.mask == "arm_only"),
                joint_names=req.joint_names, targets=None if req.targets is None else {k:v.model_dump() for k,v in req.targets.items()},
                active_joints=req.active_joints, locked_joints=req.locked_joints, attachment=None if req.attachment is None else req.attachment.model_dump(),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    @app.post("/plan/joint")
    def plan_joints(req: JointPlanRequest):
        try:
            return planner.plan_joints(
                env_id=_env(req.env_id),
                current_q=req.current_q,
                target_q=req.target_qpos,
                joint_names=req.joint_names, active_joints=req.active_joints, locked_joints=req.locked_joints, attachment=None if req.attachment is None else req.attachment.model_dump(),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    @app.post("/plan/cooperative")
    def plan_cooperative(req: CooperativeRequest):
        try:
            if req.active_joints is not None:
                raise ValueError('Common object planning requires both complete arms')
            return planner.plan_cooperative(_env(req.env_id),req.current_q,req.object_pose,req.attachment.model_dump(),req.joint_names,req.locked_joints)
        except ValueError as e:
            raise HTTPException(status_code=422,detail=str(e))

    @app.post("/plan/ik")
    def plan_ik(req: IKRequest):
        try:
            return planner.solve_ik(
                env_id=_env(req.env_id),
                target_pos=req.target_pose,
                target_quat=req.target_quat,
                lock_base=(req.mask == "arm_only"),
                joint_names=req.joint_names, targets=None if req.targets is None else {k:v.model_dump() for k,v in req.targets.items()},
                active_joints=req.active_joints, locked_joints=req.locked_joints,
                seed_q=req.seed_q,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    @app.post("/fk")
    def fk(req: FKRequest):
        try:
            return planner.fk(_env(req.env_id),req.current_q,req.joint_names,req.locked_joints,req.active_joints)
        except ValueError as e:
            raise HTTPException(status_code=422,detail=str(e))

    @app.post("/plan/validate_base_path")
    def validate_base_path(req: ValidateBaseRequest):
        try:
            return planner.validate_base_path(
                env_id=_env(req.env_id),
                base_positions=req.base_positions,
                target_pos=req.target_pos,
                base_box=req.base_box,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    return app


app = create_app()
