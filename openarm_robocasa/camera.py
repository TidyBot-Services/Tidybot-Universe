"""RGB-only recording transport, preserving native pixels and physical stamp."""
import io,json
import numpy as np


def pack_camera(rgb, state):
    buffer=io.BytesIO()
    np.savez_compressed(buffer,rgb_u8=np.uint8(np.clip(rgb,0,1)*255),
        total_steps=state['total_steps'],epoch=state['epoch'],state_json=json.dumps(state))
    return buffer.getvalue()
