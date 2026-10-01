"""Dataset registry holding the declared field catalog."""

from __future__ import annotations

from dataclasses import dataclass

from maskhub.catalog.field import Field
from maskhub.errors import Conflict, NotFound
from maskhub.store.kvstore import RecordStore

PREFIX = "dataset."


@dataclass(frozen=True)
class Dataset:
    identifier: str
    source: str
    owner: str
    fields: tuple[Field, ...]

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("dataset identifier is required")
        if not self.source.strip():
            raise ValueError("dataset source is required")
        if not self.fields:
            raise ValueError("dataset must declare at least one field")

    def field(self, name: str) -> Field | None:
        for item in self.fields:
            if item.name == name:
                return item
        return None

    def masked_fields(self) -> list[Field]:
        return [item for item in self.fields if item.sensitivity_enum.requires_masking]

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "source": self.source,
            "owner": self.owner,
            "fields": [item.as_dict() for item in self.fields],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Dataset | None":
        if not isinstance(payload, dict):
            return None
        identifier = payload.get("identifier")
        source = payload.get("source")
        owner = payload.get("owner", "")
        raw_fields = payload.get("fields")
        if not isinstance(identifier, str) or not isinstance(source, str):
            return None
        if not isinstance(owner, str) or not isinstance(raw_fields, list):
            return None
        fields = tuple(
            item for item in (Field.from_dict(raw) for raw in raw_fields) if item is not None
        )
        if len(fields) != len(raw_fields):
            return None
        try:
            return cls(identifier=identifier, source=source, owner=owner, fields=fields)
        except ValueError:
            return None


def dataset_key(identifier: str) -> str:
    return PREFIX + identifier


class DatasetRegistry:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def save(self, dataset: Dataset) -> Dataset:
        existing = self.find(dataset.identifier)
        if existing is not None and existing.source != dataset.source:
            raise Conflict(f"dataset {dataset.identifier} belongs to {existing.source}")
        self._store.put_json(dataset_key(dataset.identifier), dataset.as_dict())
        return dataset

    def find(self, identifier: str) -> Dataset | None:
        return Dataset.from_dict(self._store.get_json(dataset_key(identifier)))

    def get(self, identifier: str) -> Dataset:
        dataset = self.find(identifier)
        if dataset is None:
            raise NotFound(f"unknown dataset {identifier}")
        return dataset

    def identifiers(self) -> list[str]:
        from maskhub.store.query import keys_with_prefix

        return [key[len(PREFIX):] for key in keys_with_prefix(self._store, PREFIX)]

    def all(self) -> list[Dataset]:
        return [self.get(identifier) for identifier in self.identifiers()]

    def sensitive_surface(self) -> dict[str, list[str]]:
        return {
            dataset.identifier: [item.name for item in dataset.masked_fields()]
            for dataset in self.all()
        }
