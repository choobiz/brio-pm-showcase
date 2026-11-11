"""Background workers for brio-pm.

Currently:
- ai_worker: long-running poller that drains the `ai_jobs` queue and calls
  Anthropic, tracking cost + retries.
"""
