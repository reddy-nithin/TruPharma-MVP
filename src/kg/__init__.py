"""
src.kg — TruPharma Knowledge Graph package.

Public API:
    load_kg           (loader)
    KnowledgeGraph    (loader)
"""

from src.kg.loader import load_kg, KnowledgeGraph

__all__ = [
    "load_kg",
    "KnowledgeGraph",
]
