"""Application services: use cases composed from domain objects and ports.

A service owns one transaction boundary and one use case. It contains no SQL and
no HTTP, which is what allows the same service to back an endpoint, a worker, or
a CLI command without change.
"""
