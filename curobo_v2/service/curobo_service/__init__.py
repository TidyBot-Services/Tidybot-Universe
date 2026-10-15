"""cuRobo motion-planning HTTP service.

Wraps NVIDIA cuRobo behind a small FastAPI app so multiple callers
(sim, hardware, future skills) can share a single warmed-up instance.

See README.md for the API and setup.
"""

__version__ = "0.1.0"
