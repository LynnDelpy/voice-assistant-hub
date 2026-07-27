"""WebSocket handlers.

For M0/M1 the single `/ws/events` handler lives in api.py next to the REST
routes. As chat and audio channels arrive (M4/M7) they move here, along with the
shared Origin-allowlist check.
"""
