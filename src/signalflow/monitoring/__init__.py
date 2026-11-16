"""Instrumentation indépendante des frameworks ML et des backends de suivi."""

from .timing import Instrumentation, NullInstrumentation

__all__ = ["Instrumentation", "NullInstrumentation"]
