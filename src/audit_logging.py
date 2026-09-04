from __future__ import annotations

import json
from typing import TextIO


LEDGER_FIELDS = frozenset({
    "input_file",
    "output_file",
    "source_lang",
    "target_lang",
    "model_file",
    "model_file_sha256",
    "timestamp_utc",
    "status",
    "skip_reason",
    "input_encoding",
    "input_file_sha256",
    "normalized_source_sha256",
    "output_file_sha256",
    "source_character_count",
    "translation_character_count",
    "error_stage",
    "error_category",
})
ERROR_STAGES = frozenset({
    "read_source",
    "translate",
    "write_translation",
    "record_checksums",
})


def safe_error_details(error: Exception, stage: str) -> dict[str, str]:
    """Describe a failure without copying exception text into an audit log."""
    if stage not in ERROR_STAGES:
        stage = "other"

    if isinstance(error, PermissionError):
        category = "permission"
    elif isinstance(error, FileNotFoundError):
        category = "file_not_found"
    elif isinstance(error, UnicodeError):
        category = "text_encoding"
    elif isinstance(error, OSError):
        category = "filesystem"
    elif isinstance(error, MemoryError):
        category = "memory"
    else:
        category = "other"

    return {
        "error_stage": stage,
        "error_category": category,
    }


def write_ledger_record(stream: TextIO, record: dict) -> None:
    """Write only approved metadata fields to a translation audit log."""
    unexpected_fields = record.keys() - LEDGER_FIELDS
    if unexpected_fields:
        names = ", ".join(sorted(unexpected_fields))
        raise ValueError(f"Unsafe or unknown audit-log field(s): {names}")

    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    stream.flush()
