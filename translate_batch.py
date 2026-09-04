from pathlib import Path
from datetime import datetime, timezone
import json
import os
import platform
import sys
from tqdm import tqdm
import logging

import argostranslate.package
import argostranslate.translate

from src.argos_batch_helpers import (
    SUPPORTED_INPUT_SUFFIXES,
    ensure_argos_model,
    find_input_files,
    get_package_version,
    normalize_text,
    read_source_file,
    sha256_bytes,
    sha256_file,
    sha256_text,
    validate_unique_input_stems,
)
from src.audit_logging import safe_error_details, write_ledger_record
from src.redaction_logbook import generate_redaction_logbook

SOURCE_LANG = "nl"
TARGET_LANG = "en"

INPUT_DIR = Path("corpora/raw")
OUTPUT_DIR = Path("corpora/translated")
REDACTED_DIR = Path("corpora/redacted")
LOG_DIR = Path("logs")
MODEL_DIR = Path("models")

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REDACTED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

input_files = find_input_files(INPUT_DIR)
validate_unique_input_stems(input_files)


MODEL_FILE = ensure_argos_model(
    from_code=SOURCE_LANG,
    to_code=TARGET_LANG,
    model_dir=MODEL_DIR,
)

argostranslate.package.install_from_path(MODEL_FILE)

translation = argostranslate.translate.get_translation_from_codes(
    SOURCE_LANG,
    TARGET_LANG,
)

model_sha256 = sha256_file(MODEL_FILE)

model_metadata_path = MODEL_DIR / "model_metadata.json"
model_metadata = None

if model_metadata_path.exists():
    model_metadata = json.loads(model_metadata_path.read_text(encoding="utf-8"))

manifest = {
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "source_lang": SOURCE_LANG,
    "target_lang": TARGET_LANG,
    "input_dir": str(INPUT_DIR),
    "output_dir": str(OUTPUT_DIR),
    "redacted_dir": str(REDACTED_DIR),
    "supported_input_formats": sorted(SUPPORTED_INPUT_SUFFIXES),
    "model_file": str(MODEL_FILE),
    "model_file_sha256": model_sha256,
    "model_metadata": model_metadata,
    "environment": {
        "python": sys.version,
        "platform": platform.platform(),
        "argostranslate": get_package_version("argostranslate"),
        "ctranslate2": get_package_version("ctranslate2"),
        "python-docx": get_package_version("python-docx"),
        "sentencepiece": get_package_version("sentencepiece"),
        "ARGOS_DEVICE_TYPE": os.getenv("ARGOS_DEVICE_TYPE"),
        "ARGOS_PACKAGES_DIR": os.getenv("ARGOS_PACKAGES_DIR"),
        "ARGOS_CHUNK_TYPE": os.getenv("ARGOS_CHUNK_TYPE"),
        "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"),
        "PYTHONHASHSEED": os.getenv("PYTHONHASHSEED"),
    },
}

(LOG_DIR / "translation_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

ledger_path = LOG_DIR / "translation_ledger.jsonl"
error_path = LOG_DIR / "errors.jsonl"
redaction_logbook_path = REDACTED_DIR / "redaction_logbook.xlsx"

translated_count = 0
skipped_count = 0
error_count = 0

# Disable spurious mwt warning
logging.getLogger("stanza").disabled = True

with ledger_path.open("w", encoding="utf-8") as ledger, error_path.open("w", encoding="utf-8") as errors:
    for input_path in tqdm(
        input_files,
        desc="Translating files",
        unit="file",
    ):
        output_path = OUTPUT_DIR / f"{input_path.stem}.{TARGET_LANG}.txt"

        record = {
            "input_file": str(input_path),
            "input_format": input_path.suffix.lower().removeprefix("."),
            "output_file": str(output_path),
            "source_lang": SOURCE_LANG,
            "target_lang": TARGET_LANG,
            "model_file": str(MODEL_FILE),
            "model_file_sha256": model_sha256,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

        if output_path.is_file():
            record.update({
                "status": "skipped",
                "skip_reason": "translated output already exists",
            })
            write_ledger_record(ledger, record)
            skipped_count += 1
            continue

        stage = "read_source"
        try:
            raw_bytes = input_path.read_bytes()
            raw_sha256 = sha256_bytes(raw_bytes)

            raw_text, encoding = read_source_file(input_path)
            normalized_text = normalize_text(raw_text)

            stage = "translate"
            translated_text = translation.translate(normalized_text)

            stage = "write_translation"
            output_path.write_text(translated_text, encoding="utf-8")

            stage = "record_checksums"
            record.update({
                "status": "ok",
                "input_encoding": encoding,
                "input_file_sha256": raw_sha256,
                "normalized_source_sha256": sha256_text(normalized_text),
                "output_file_sha256": sha256_file(output_path),
                "source_character_count": len(normalized_text),
                "translation_character_count": len(translated_text),
            })

            write_ledger_record(ledger, record)

            translated_count += 1

        except Exception as e:
            record.update({
                "status": "error",
                **safe_error_details(e, stage),
            })

            write_ledger_record(errors, record)
            write_ledger_record(ledger, record)

            error_count += 1

if skipped_count:
    print(
        f"Skipped translation for {skipped_count} existing translated file(s). "
        f"To translate them again, delete the corresponding files from "
        f"'{OUTPUT_DIR}' and '{REDACTED_DIR}', then rerun the script.",
        file=sys.stderr,
    )

redaction_count, unmatched_redacted_files = generate_redaction_logbook(
    translated_dir=OUTPUT_DIR,
    redacted_dir=REDACTED_DIR,
    logbook_path=redaction_logbook_path,
)

print(
    json.dumps(
        {
            "status": "done",
            "translated_files": translated_count,
            "skipped_translations": skipped_count,
            "error_files": error_count,
            "redacted_passages": redaction_count,
            "unmatched_redacted_files": unmatched_redacted_files,
            "model_file": str(MODEL_FILE),
            "ledger": str(ledger_path),
            "errors": str(error_path),
            "manifest": str(LOG_DIR / "translation_manifest.json"),
            "redaction_logbook": str(redaction_logbook_path),
        },
        ensure_ascii=False,
        indent=2,
    )
)
