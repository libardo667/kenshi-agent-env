"""Optional EvoGen subject adapter for KAE.

This package is deliberately not imported by :mod:`kenshi_agent`.  Every
EvoGen import is local to a factory or role method so ordinary KAE operation
does not acquire an EvoGen runtime dependency.
"""

from __future__ import annotations

from .adapter import build_subject_plugin

__all__ = ["build_subject_plugin"]
