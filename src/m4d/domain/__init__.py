"""The domain layer.

This package holds the business vocabulary: entities, value objects, errors, and
the abstract ports through which the domain reaches the outside world.

It depends on nothing else in the application. No FastAPI, no SQLAlchemy, no
settings. That constraint is what makes the domain testable without a database
and what keeps infrastructure decisions from leaking into business rules.
"""
