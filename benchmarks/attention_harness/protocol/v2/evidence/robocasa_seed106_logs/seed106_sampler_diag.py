"""One-process seed-106 placement diagnostic; source files stay unchanged."""

from collections import Counter
import os

from mani_skill.utils.scene_builder.robocasa.utils.placement_samplers import (
    RandomizationError,
    UniformRandomSampler,
)
from maniskill_server.server import ManiskillServer

if os.environ.get("PROBE_COUNTER_REGIONS") == "1":
    from robocasa_tasks.single_stage.kitchen_pnp import PnPCounterToCab

    original_cfgs = PnPCounterToCab._get_obj_cfgs

    def probe_cfgs(self):
        cfgs = original_cfgs(self)
        for cfg in cfgs:
            if cfg["name"] in {"obj", "distr_counter"}:
                cfg["placement"]["sample_region_kwargs"] = {}
                if os.environ.get("PROBE_KEEP_POS") != "1":
                    cfg["placement"]["pos"] = (0.0, 0.0)
                    cfg["placement"]["offset"] = (0.0, 0.0)
        return cfgs

    PnPCounterToCab._get_obj_cfgs = probe_cfgs


original_sample = UniformRandomSampler.sample
attempts = Counter()


def observed_sample(self, *args, **kwargs):
    if self.name == "obj_Sampler":
        attempts[self.name] += 1
        if attempts[self.name] <= 12:
            print("TARGET_SAMPLER", attempts[self.name],
                  "x_range", list(self.x_range),
                  "y_range", list(self.y_range),
                  "reference_pos", list(self.reference_pos),
                  "reference_rot", self.reference_rot,
                  "placed_names", list(kwargs.get("placed_objects", {})), flush=True)
    try:
        return original_sample(self, *args, **kwargs)
    except RandomizationError:
        attempts["failure:" + self.name] += 1
        raise


UniformRandomSampler.sample = observed_sample
server = ManiskillServer(task="RoboCasa-Pn-P-Counter-To-Cab-v0", seed=106)
server._init_env()
print("SAMPLER_COUNTS", dict(attempts), flush=True)
print("OBJECT_ACTORS", [(index, list(value)) for index, value in
                        enumerate(server.env.unwrapped.object_actors)], flush=True)
