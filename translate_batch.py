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
    ensure_argos_model,
    get_package_version,
    normalize_text,
    read_text_file,
    sha256_bytes,
    sha256_file,
    sha256_text,
)
from src.nllb_translation import (
    DEFAULT_NLLB_MODEL_ID,
    NllbTranslator,
    convert_nllb_model_for_cpu,
    download_nllb_model,
)

SOURCE_LANG = "nl"
TARGET_LANG = "en"

# Keep Argos as the default so existing runs do not change. Set the environment
# variable TRANSLATION_BACKEND=nllb to use Meta NLLB-200 on the CPU.
TRANSLATION_BACKEND = os.getenv("TRANSLATION_BACKEND", "argos").strip().lower()
NLLB_MODEL_ID = os.getenv("NLLB_MODEL_ID", DEFAULT_NLLB_MODEL_ID).strip()
NLLB_BATCH_SIZE = (
    int(os.getenv("NLLB_BATCH_SIZE", "8"))
    if TRANSLATION_BACKEND == "nllb"
    else None
)

INPUT_DIR = Path("corpora/raw")
OUTPUT_DIR = Path("corpora/translated")
LOG_DIR = Path("logs")
MODEL_DIR = Path("models")

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)


if TRANSLATION_BACKEND == "argos":
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
elif TRANSLATION_BACKEND == "nllb":
    nllb_snapshot_path, source_model_metadata = download_nllb_model(
        NLLB_MODEL_ID,
        MODEL_DIR,
    )
    MODEL_FILE, model_metadata = convert_nllb_model_for_cpu(
        snapshot_path=nllb_snapshot_path,
        source_metadata=source_model_metadata,
        model_dir=MODEL_DIR,
    )
    model_sha256 = model_metadata["sha256"]
    translation = NllbTranslator.load(
        model_path=MODEL_FILE,
        tokenizer_path=nllb_snapshot_path,
        source_language_code=SOURCE_LANG,
        target_language_code=TARGET_LANG,
        batch_size=NLLB_BATCH_SIZE,
    )
else:
    raise ValueError(
        f"Unknown TRANSLATION_BACKEND {TRANSLATION_BACKEND!r}; use 'argos' or 'nllb'"
    )

manifest = {
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "translation_backend": TRANSLATION_BACKEND,
    "source_lang": SOURCE_LANG,
    "target_lang": TARGET_LANG,
    "input_dir": str(INPUT_DIR),
    "output_dir": str(OUTPUT_DIR),
    "model_file": str(MODEL_FILE),
    "model_file_sha256": model_sha256,
    "model_metadata": model_metadata,
    "environment": {
        "python": sys.version,
        "platform": platform.platform(),
        "argostranslate": get_package_version("argostranslate"),
        "ctranslate2": get_package_version("ctranslate2"),
        "sentencepiece": get_package_version("sentencepiece"),
        "transformers": get_package_version("transformers"),
        "huggingface_hub": get_package_version("huggingface-hub"),
        "torch": get_package_version("torch"),
        "ARGOS_DEVICE_TYPE": os.getenv("ARGOS_DEVICE_TYPE"),
        "ARGOS_PACKAGES_DIR": os.getenv("ARGOS_PACKAGES_DIR"),
        "ARGOS_CHUNK_TYPE": os.getenv("ARGOS_CHUNK_TYPE"),
        "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"),
        "PYTHONHASHSEED": os.getenv("PYTHONHASHSEED"),
        "NLLB_MODEL_ID": NLLB_MODEL_ID if TRANSLATION_BACKEND == "nllb" else None,
        "NLLB_BATCH_SIZE": NLLB_BATCH_SIZE if TRANSLATION_BACKEND == "nllb" else None,
    },
}

(LOG_DIR / "translation_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

ledger_path = LOG_DIR / "translation_ledger.jsonl"
error_path = LOG_DIR / "errors.jsonl"

translated_count = 0
error_count = 0

# Disable spurious mwt warning
logging.getLogger("stanza").disabled = True

with ledger_path.open("w", encoding="utf-8") as ledger, error_path.open("w", encoding="utf-8") as errors:
    input_files = sorted(INPUT_DIR.glob("*.txt"))

    for input_path in tqdm(
        input_files,
        desc="Translating files",
        unit="file",
    ):
        output_path = OUTPUT_DIR / f"{input_path.stem}.{TARGET_LANG}.txt"

        record = {
            "input_file": str(input_path),
            "output_file": str(output_path),
            "source_lang": SOURCE_LANG,
            "target_lang": TARGET_LANG,
            "translation_backend": TRANSLATION_BACKEND,
            "model_file": str(MODEL_FILE),
            "model_file_sha256": model_sha256,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            raw_bytes = input_path.read_bytes()
            raw_sha256 = sha256_bytes(raw_bytes)

            raw_text, encoding = read_text_file(input_path)
            normalized_text = normalize_text(raw_text)

            translated_text = translation.translate(normalized_text)
            output_path.write_text(translated_text, encoding="utf-8")

            record.update({
                "status": "ok",
                "input_encoding": encoding,
                "input_file_sha256": raw_sha256,
                "normalized_source_sha256": sha256_text(normalized_text),
                "output_file_sha256": sha256_file(output_path),
                "source_character_count": len(normalized_text),
                "translation_character_count": len(translated_text),
                "error_message": None,
            })

            ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
            ledger.flush()

            translated_count += 1

        except Exception as e:
            record.update({
                "status": "error",
                "error_message": repr(e),
            })

            errors.write(json.dumps(record, ensure_ascii=False) + "\n")
            errors.flush()

            ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
            ledger.flush()

            error_count += 1

print(
    json.dumps(
        {
            "status": "done",
            "translated_files": translated_count,
            "error_files": error_count,
            "model_file": str(MODEL_FILE),
            "ledger": str(ledger_path),
            "errors": str(error_path),
            "manifest": str(LOG_DIR / "translation_manifest.json"),
        },
        ensure_ascii=False,
        indent=2,
    )
)
