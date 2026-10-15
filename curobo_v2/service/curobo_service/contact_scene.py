"""GPU collision groups for explicit carried-object contact pairs only."""
from dataclasses import replace
import torch
from curobo._src.geom.collision.collision_scene import SceneCollision,SceneCollisionCfg
from curobo._src.geom.collision.buffer_collision import CollisionBuffer


class CarriedSceneCollision(SceneCollision):
    def configure(self,attached_indices,total_spheres,cache):
        self._attached_indices=tuple(sorted(set(attached_indices)))
        if not self._attached_indices or min(self._attached_indices)<0 or max(self._attached_indices)>=total_spheres:raise ValueError('Invalid attachment sphere mapping')
        mask=torch.zeros(total_spheres,dtype=torch.bool,device=self.device_cfg.device);mask[list(self._attached_indices)]=True
        self._masks=(~mask,mask);self._buffers={};self._object_names=set();self._support_names=set()
        self._groups=[SceneCollision.from_config(SceneCollisionCfg(device_cfg=self.device_cfg,cache=cache))for _ in range(2)]
        self.load_collision_model(self.scene_model)

    def attachment(self,object_names,support_names):
        self._object_names=set(object_names);self._support_names=set(support_names)
        self.load_collision_model(self.scene_model)

    def load_collision_model(self,scene_model,env_idx=0):
        if env_idx!=0 or isinstance(scene_model,list):raise ValueError('Measured contact groups require one explicit world')
        super().load_collision_model(scene_model,env_idx)
        if not hasattr(self,'_groups'):return
        if scene_model.mesh or scene_model.voxel:raise ValueError('Contact groups require the declared complete cuboid world')
        names={c.name for c in scene_model.cuboid or []}
        if not self._object_names|self._support_names<=names:raise ValueError('Contact pair geometry absent from complete world')
        for index,group in enumerate(self._groups):
            excluded=self._object_names|(self._support_names if index==1 else set())
            group.load_collision_model(replace(scene_model,cuboid=[c for c in scene_model.cuboid or []if c.name not in excluded]))

    def _query(self,method,state,collision_buffer,**kwargs):
        if not hasattr(self,'_groups'):raise ValueError('Attachment groups must be configured before CUDA capture')
        spheres=state.robot_spheres
        if spheres.shape[-2]!=len(self._masks[0]):raise ValueError('Attachment sphere mapping changed')
        results=[]
        for i,(group,mask)in enumerate(zip(self._groups,self._masks)):
            key=(id(collision_buffer),i)
            buffer=self._buffers.get(key)
            if buffer is None:buffer=self._buffers[key]=CollisionBuffer.from_shape(spheres.shape,self.device_cfg)
            buffer.resize(spheres.shape,self.device_cfg)
            # Every normal sphere checks every static shape. Carried spheres
            # check every static shape except explicitly declared native departure
            # supports. Reparented shapes remain in the carried robot geometry.
            query=torch.cat((spheres[...,:3],torch.where(mask,spheres[...,3],-100.).unsqueeze(-1)),dim=-1)
            results.append(getattr(group.checker,method)(scene=group.data,query_sphere=query,collision_buffer=buffer,**kwargs))
        return results[0]+results[1]

    def get_sphere_distance(self,state,collision_buffer,weight,activation_distance,env_query_idx=None,return_loss=False):
        return self._query('get_sphere_distance',state,collision_buffer,weight=weight,activation_distance=activation_distance,env_query_idx=env_query_idx,return_loss=return_loss)

    get_sphere_collision=get_sphere_distance

    def get_swept_sphere_distance(self,state,collision_buffer,weight,activation_distance,trajectory_dt,enable_speed_metric=False,env_query_idx=None,return_loss=False):
        return self._query('get_swept_sphere_distance',state,collision_buffer,weight=weight,activation_distance=activation_distance,trajectory_dt=trajectory_dt,enable_speed_metric=enable_speed_metric,env_query_idx=env_query_idx,return_loss=return_loss)

    get_swept_sphere_collision=get_swept_sphere_distance
