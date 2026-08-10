"""The HTTP delivery layer.

This is the only package that knows about status codes, headers, and JSON
shapes. It translates HTTP into service calls and domain errors back into
responses; it holds no business logic of its own.
"""
