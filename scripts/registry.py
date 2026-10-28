"""registry.py - Loads the closed vocabulary from ENTITY_TYPE_REGISTRY.md."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

import paths


class RegistryError(Exception):
    """Raised when registry is malformed or a lookup fails."""


class Registry:
    """The closed vocabulary for entity types, relationships, source types, and enumerations."""

    def __init__(self, data: dict[str, Any]):
        self._data = data
        self._entity_types = data["entity_types"]
        self._rel_inverses = data["relationship_inverses"]
        self._source_conf = data["source_confidence"]
        # derivation_types is optional in the JSON for tolerance toward pre-2026-09 registries
        # that predate the source/derived split; an absent list means no derivation is accepted.
        self.derivation_types = data.get("derivation_types", [])
        self.confidence_levels = data["confidence_levels"]
        # temporal_classes governs fact/relationship staleness expectation. Optional in the
        # JSON for tolerance toward pre-2026-08 registries; falls back to the canonical three.
        self.temporal_classes = data.get("temporal_classes", ["permanent", "mutable", "volatile"])
        self.entity_statuses = data["entity_statuses"]
        self.evidence_types = data["evidence_types"]
        self.perspective_statuses = data["perspective_statuses"]
        self.conflict_statuses = data["conflict_statuses"]

    def is_entity_type(self, entity_type: str) -> bool:
        return entity_type in self._entity_types

    def directory_for(self, entity_type: str) -> str:
        """Return the directory name for an entity type."""
        if entity_type not in self._entity_types:
            import traceback
            stack = ''.join(traceback.format_stack())
            raise RegistryError(f"unknown entity type: {entity_type!r}\nCall stack:\n{stack}")
        return self._entity_types[entity_type]["directory"]

    def is_relationship(self, rel_type: str) -> bool:
        return rel_type in self._rel_inverses

    def inverse_of(self, rel_type: str) -> str | None:
        """Return the inverse relationship type, or None if symmetric."""
        if rel_type not in self._rel_inverses:
            raise RegistryError(f"unknown relationship type: {rel_type!r}")
        return self._rel_inverses[rel_type]

    def is_source_type(self, source: str) -> bool:
        return source in self._source_conf

    def default_confidence(self, source: str) -> str:
        """Return the default confidence level for a source type."""
        if source not in self._source_conf:
            raise RegistryError(f"unknown source type: {source!r}")
        return self._source_conf[source]

    def is_derivation_type(self, derived: str) -> bool:
        return derived in self.derivation_types

    def discount_confidence(self, conf: str) -> str:
        """Lower a confidence one step, clamped at the weakest level.

        Used when a fact carries `derived=`: it was read from something standing in for the
        source, so it cannot be as trustworthy as the source itself. Mechanical on purpose —
        an agent must not be the one deciding how much a summary costs.
        """
        if conf not in self.confidence_levels:
            raise RegistryError(f"unknown confidence level: {conf!r}")
        i = self.confidence_levels.index(conf)
        return self.confidence_levels[min(i + 1, len(self.confidence_levels) - 1)]

    def is_confidence(self, conf: str) -> bool:
        return conf in self.confidence_levels

    def is_temporal_class(self, tc: str) -> bool:
        return tc in self.temporal_classes


def load_registry(path: Path | None = None) -> Registry:
    """Load and parse the registry from ENTITY_TYPE_REGISTRY.md."""
    path = path or paths.REGISTRY_FILE
    if not path.exists():
        raise RegistryError(f"registry file not found: {path}")

    content = path.read_text(encoding="utf-8")

    # Extract the JSON block between ```json and ```
    match = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
    if not match:
        raise RegistryError(f"no JSON block found in {path}")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise RegistryError(f"invalid JSON in registry: {e}") from e

    return Registry(data)


if __name__ == "__main__":
    # Quick validation
    reg = load_registry()
    print(f"Loaded registry v{reg._data['registry_version']}")
    print(f"  Entity types: {len(reg._entity_types)}")
    print(f"  Relationships: {len(reg._rel_inverses)}")
    print(f"  Source types: {len(reg._source_conf)}")
