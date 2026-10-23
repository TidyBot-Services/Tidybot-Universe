"""Fresh-process scene reset only, using the experiment reset path. No policy."""
def main():
    import json,sys
    from pathlib import Path
    from openarm365_dev_v2.formal_config import validate_native_config
    from openarm365_dev_v2.environment import create_env
    from openarm365.environment import validate_physical_model,configure_display_camera
    from openarm365_dev_v2.scene_contract import capture_state
    root=Path(sys.argv[1]);c=validate_native_config(json.loads((root/'config.json').read_bytes()))
    env=create_env(c['official_env'],robots='OpenArm365',seed=c['seed'],obj_instance_split=c['obj_instance_split'],layout_and_style_ids=[(c['layout_id'],c['style_id'])],camera_widths=128,camera_heights=128,randomize_cameras=False,generative_textures=None,control_freq=c['control_freq'],horizon=c['horizon'],**({'scene_reset_seed':c['scene_reset_seed']} if 'scene_reset_seed' in c else {}))
    try:
        env.reset();validate_physical_model(env);configure_display_camera(env);state=capture_state(env)
        observation={'robot':state['robot'],'perception':{'values':state['objects']}}
        (root/'result.json').write_text(json.dumps(dict(observation=observation,state=state),allow_nan=False))
    finally:env.close()


if __name__=='__main__':main()
