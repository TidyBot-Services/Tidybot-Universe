from __future__ import annotations

import base64

import numpy as np

from benchmarks.attention_harness.model_protocol import extract_python, image_data_url


def test_extract_python_fence() -> None:
    assert extract_python("before```python\nprint('ok')\n```after") == "print('ok')\n"


def test_png_data_url_has_real_png_signature() -> None:
    url = image_data_url(np.zeros((3, 4, 3), dtype=np.uint8))
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")
