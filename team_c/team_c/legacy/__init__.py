"""Retired features kept only as read-only compatibility for historical records.

code_discovery: local Python/FastAPI code discovery. The routes return 410 code_discovery_retired; it is
kept so existing code inventories stay readable and grounding can verify their provenance
(validate_code_provenance). No new flow should depend on it.
"""
