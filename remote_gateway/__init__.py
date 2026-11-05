"""Public gateway: browsers reach this process only, and it reaches one harness over a signed loopback link.

It lives outside benchmarks/attention_harness so that changing it never counts as a change to the harness sources a run
has frozen. See README.md for what it does and what still has to happen before it may face other people.
"""
