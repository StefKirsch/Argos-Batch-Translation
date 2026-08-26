from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
from typing import Any


DEFAULT_NLLB_MODEL_ID = "facebook/nllb-200-distilled-600M"

# Keep the simple ISO 639-1 settings used by this project for common languages.
# A FLORES-200 code can always be supplied directly for any other language.
NLLB_LANGUAGE_ALIASES = {
    "ar": "arb_Arab",
    "cs": "ces_Latn",
    "da": "dan_Latn",
    "de": "deu_Latn",
    "el": "ell_Grek",
    "en": "eng_Latn",
    "es": "spa_Latn",
    "fi": "fin_Latn",
    "fr": "fra_Latn",
    "it": "ita_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "nl": "nld_Latn",
    "no": "nob_Latn",
    "pl": "pol_Latn",
    "pt": "por_Latn",
    "ro": "ron_Latn",
    "ru": "rus_Cyrl",
    "sv": "swe_Latn",
    "tr": "tur_Latn",
    "uk": "ukr_Cyrl",
    "zh": "zho_Hans",
}


def resolve_nllb_language_code(language_code: str) -> str:
    code = language_code.strip()
    if not code:
        raise ValueError("The NLLB language code cannot be empty")
    return NLLB_LANGUAGE_ALIASES.get(code.lower(), code)


def model_directory_name(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "--", model_id)


def sha256_model_files(model_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Hash all runtime files in a local model directory."""
    ignored_parts = {".cache", "__pycache__"}
    files = sorted(
        path
        for path in model_path.rglob("*")
        if path.is_file() and not ignored_parts.intersection(path.parts)
    )
    if not files:
        raise RuntimeError(f"No model files found in {model_path}")

    combined = hashlib.sha256()
    file_records = []
    for path in files:
        relative_path = path.relative_to(model_path).as_posix()
        file_hash = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_hash.update(block)
        digest = file_hash.hexdigest()
        combined.update(relative_path.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\n")
        file_records.append(
            {
                "path": relative_path,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
            }
        )

    return combined.hexdigest(), file_records


def download_nllb_model(model_id: str, model_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Download or reuse an NLLB snapshot without prompting the user."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "NLLB requires the dependencies in requirements.txt. "
            "Run 'uv pip install -r requirements.txt'."
        ) from exc

    cache_dir = model_dir / "huggingface"
    cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = Path(
        snapshot_download(
            repo_id=model_id,
            cache_dir=cache_dir,
            # Download only files that Transformers may need for PyTorch inference.
            allow_patterns=[
                "*.bin",
                "*.json",
                "*.model",
                "*.safetensors",
                "*.txt",
            ],
        )
    )

    model_sha256, files = sha256_model_files(snapshot_path)
    metadata = {
        "selection_rule": "configured NLLB model; default is the smallest distilled NLLB-200 checkpoint",
        "model_id": model_id,
        "revision": snapshot_path.name,
        "saved_model_directory": str(snapshot_path),
        "sha256": model_sha256,
        "files": files,
    }

    return snapshot_path, metadata


def convert_nllb_model_for_cpu(
    snapshot_path: Path,
    source_metadata: dict[str, Any],
    model_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Convert the downloaded checkpoint once to portable CPU INT8 format."""
    try:
        import ctranslate2
        from ctranslate2.converters import TransformersConverter
    except ImportError as exc:
        raise RuntimeError(
            "NLLB requires the dependencies in requirements.txt. "
            "Run 'uv pip install -r requirements.txt'."
        ) from exc

    revision = source_metadata["revision"]
    output_dir = (
        model_dir
        / "ctranslate2"
        / f"{model_directory_name(source_metadata['model_id'])}--{revision[:12]}--int8"
    )
    conversion_metadata_path = output_dir / "conversion_metadata.json"
    existing_conversion = None
    if conversion_metadata_path.exists():
        try:
            existing_conversion = json.loads(
                conversion_metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            existing_conversion = None

    expected_conversion = {
        "source_model_id": source_metadata["model_id"],
        "source_revision": revision,
        "runtime": "CTranslate2",
        "ctranslate2_version": ctranslate2.__version__,
        "quantization": "int8",
        "device": "cpu",
    }
    required_files = (output_dir / "model.bin", output_dir / "config.json")
    if existing_conversion != expected_conversion or not all(
        path.exists() for path in required_files
    ):
        output_dir.mkdir(parents=True, exist_ok=True)
        converter = TransformersConverter(str(snapshot_path))
        converter.convert(
            str(output_dir),
            quantization="int8",
            force=True,
        )
        conversion_metadata_path.write_text(
            json.dumps(expected_conversion, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    model_sha256, converted_files = sha256_model_files(output_dir)
    metadata = {
        "selection_rule": (
            "configured NLLB model converted to CTranslate2 INT8 for portable, "
            "fast CPU inference; default source is the smallest distilled NLLB-200 checkpoint"
        ),
        "model_id": source_metadata["model_id"],
        "revision": revision,
        "source_snapshot_directory": str(snapshot_path),
        "source_snapshot_sha256": source_metadata["sha256"],
        "source_files": source_metadata["files"],
        "saved_model_directory": str(output_dir),
        "runtime": expected_conversion,
        "sha256": model_sha256,
        "files": converted_files,
    }
    (model_dir / "nllb_model_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (model_dir / "NLLB_SHA256SUMS.txt").write_text(
        "".join(
            f"{item['sha256']}  {item['path']}\n" for item in converted_files
        ),
        encoding="utf-8",
    )
    return output_dir, metadata


@dataclass
class NllbTranslator:
    tokenizer: Any
    translator: Any
    target_language_code: str
    batch_size: int = 8
    max_source_tokens: int = 512

    @classmethod
    def load(
        cls,
        model_path: Path,
        tokenizer_path: Path,
        source_language_code: str,
        target_language_code: str,
        batch_size: int = 8,
    ) -> NllbTranslator:
        try:
            import ctranslate2
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "NLLB requires the dependencies in requirements.txt. "
                "Run 'uv pip install -r requirements.txt'."
            ) from exc

        if batch_size < 1:
            raise ValueError("NLLB_BATCH_SIZE must be at least 1")

        source_code = resolve_nllb_language_code(source_language_code)
        target_code = resolve_nllb_language_code(target_language_code)
        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_path,
            src_lang=source_code,
            tgt_lang=target_code,
        )
        for configured, resolved in (
            (source_language_code, source_code),
            (target_language_code, target_code),
        ):
            if tokenizer.convert_tokens_to_ids(resolved) == tokenizer.unk_token_id:
                raise ValueError(
                    f"Language {configured!r} resolves to {resolved!r}, which is not "
                    "supported by this NLLB model. Use a supported FLORES-200 code."
                )

        translator = ctranslate2.Translator(
            str(model_path),
            device="cpu",
            compute_type="int8",
        )

        # NLLB was trained with source sequences no longer than 512 tokens.
        special_tokens = tokenizer.num_special_tokens_to_add(pair=False)
        max_source_tokens = max(1, 512 - special_tokens)
        return cls(
            tokenizer=tokenizer,
            translator=translator,
            target_language_code=target_code,
            batch_size=batch_size,
            max_source_tokens=max_source_tokens,
        )

    def _line_chunks(self, line: str) -> list[str]:
        token_ids = self.tokenizer.encode(line, add_special_tokens=False)
        if not token_ids:
            return []
        return [
            self.tokenizer.decode(
                token_ids[start:start + self.max_source_tokens],
                skip_special_tokens=True,
            )
            for start in range(0, len(token_ids), self.max_source_tokens)
        ]

    def translate(self, text: str) -> str:
        lines = text.split("\n")
        chunks: list[str] = []
        chunks_per_line: list[int] = []
        for line in lines:
            line_chunks = self._line_chunks(line) if line.strip() else []
            chunks.extend(line_chunks)
            chunks_per_line.append(len(line_chunks))

        translated_chunks: list[str] = []
        for start in range(0, len(chunks), self.batch_size):
            batch = chunks[start:start + self.batch_size]
            source_tokens = [
                self.tokenizer.convert_ids_to_tokens(
                    self.tokenizer.encode(chunk)
                )
                for chunk in batch
            ]
            results = self.translator.translate_batch(
                source_tokens,
                target_prefix=[[self.target_language_code]] * len(source_tokens),
                max_batch_size=self.batch_size,
                beam_size=1,
                max_input_length=512,
                max_decoding_length=512,
            )
            for result in results:
                # CTranslate2 includes the forced target-language prefix.
                target_tokens = result.hypotheses[0][1:]
                translated_chunks.append(
                    self.tokenizer.decode(
                        self.tokenizer.convert_tokens_to_ids(target_tokens),
                        skip_special_tokens=True,
                    )
                )

        translated_lines = []
        position = 0
        for chunk_count in chunks_per_line:
            translated_lines.append(
                " ".join(translated_chunks[position:position + chunk_count])
            )
            position += chunk_count
        return "\n".join(translated_lines)
