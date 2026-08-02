"""Shared Pydantic base for LLM-facing Maps enrichment models.

Root cause of the reported 143/143 Sonar classify failures: several result
models declare ``Field(..., max_length=N)`` on free-text fields (evidence
quotes, reasons, operator/ownership descriptors). Pydantic's ``max_length``
constraint *raises* ValidationError when the model's real-world output is
longer than N — it does not truncate. Every downstream call site already
truncates the value with ``value[:N]`` before using it, so the validation
constraint was strictly harmful: it discarded an otherwise perfectly usable
classification the moment a quote or descriptor ran one character too long.

``TruncatingModel`` truncates over-long strings to their declared
``max_length`` *before* validation instead of rejecting them, so a slightly
verbose provider response becomes a successful classification instead of a
parse failure.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


class TruncatingModel(BaseModel):
    """BaseModel that truncates strings to each field's ``max_length`` instead
    of raising a ValidationError when a provider response is verbose."""

    @model_validator(mode="before")
    @classmethod
    def _truncate_long_strings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = dict(data)
        for name, field in cls.model_fields.items():
            keys = {name}
            alias = getattr(field, "validation_alias", None)
            if alias is not None:
                choices = getattr(alias, "choices", None)
                if choices:
                    keys.update(str(c) for c in choices)
                else:
                    keys.add(str(alias))
            for key in keys:
                if key not in cleaned:
                    continue
                value = cleaned[key]
                if not isinstance(value, str):
                    continue
                max_len = None
                for meta in field.metadata:
                    candidate = getattr(meta, "max_length", None)
                    if candidate is not None:
                        max_len = candidate
                        break
                if max_len is not None and len(value) > max_len:
                    cleaned[key] = value[:max_len]
        return cleaned


__all__ = ["TruncatingModel"]
