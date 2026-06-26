from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile

import argostranslate.package


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def version_key(value) -> tuple:
    if value is None:
        return tuple()
    return tuple(int(x) for x in re.findall(r"\d+", str(value)))


def safe_json(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def get_package_version(pkg: str) -> str | None:
    try:
        out = subprocess.check_output(
            [sys.executable, "-m", "pip", "show", pkg],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        return None
    return None


def read_text_file(path: Path) -> tuple[str, str]:
    return path.read_text(encoding="utf-8"), "utf-8"


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_argosmodel_metadata(model_file: Path) -> dict | None:
    if not model_file.exists():
        return None

    if not zipfile.is_zipfile(model_file):
        return None

    try:
        with zipfile.ZipFile(model_file, "r") as zf:
            metadata_files = [
                name for name in zf.namelist()
                if name.endswith("metadata.json")
            ]

            if not metadata_files:
                return None

            with zf.open(metadata_files[0]) as f:
                return json.loads(f.read().decode("utf-8"))

    except Exception:
        return None


def metadata_language_codes(metadata: dict | None) -> tuple[str | None, str | None]:
    if not metadata:
        return None, None

    from_code = (
        metadata.get("from_code")
        or metadata.get("from")
        or metadata.get("source_code")
        or metadata.get("source")
    )

    to_code = (
        metadata.get("to_code")
        or metadata.get("to")
        or metadata.get("target_code")
        or metadata.get("target")
    )

    return from_code, to_code


def model_is_suitable(model_file: Path, from_code: str, to_code: str) -> bool:
    if not model_file.exists():
        return False

    if model_file.suffix != ".argosmodel":
        return False

    if not zipfile.is_zipfile(model_file):
        return False

    metadata = read_argosmodel_metadata(model_file)
    metadata_from_code, metadata_to_code = metadata_language_codes(metadata)

    if metadata_from_code and metadata_to_code:
        return metadata_from_code == from_code and metadata_to_code == to_code

    expected_name = f"translate-{from_code}_{to_code}.argosmodel"
    return model_file.name == expected_name


def download_best_argos_model(
    from_code: str,
    to_code: str,
    model_dir: Path,
) -> Path:
    model_dir.mkdir(parents=True, exist_ok=True)

    argostranslate.package.update_package_index()
    available_packages = argostranslate.package.get_available_packages()

    matches = [
        p for p in available_packages
        if p.from_code == from_code and p.to_code == to_code
    ]

    if not matches:
        raise RuntimeError(
            f"No direct Argos-compatible model found for {from_code}->{to_code}"
        )

    matches = sorted(
        matches,
        key=lambda p: (
            version_key(getattr(p, "package_version", "")),
            version_key(getattr(p, "argos_version", "")),
        ),
        reverse=True,
    )

    selected = matches[0]

    downloaded_path = Path(selected.download())
    target_path = model_dir / f"translate-{from_code}_{to_code}.argosmodel"

    shutil.copy2(downloaded_path, target_path)

    sha256 = sha256_file(target_path)

    metadata = {
        "selection_rule": (
            "highest package_version, then highest argos_version, "
            "among direct Argos-compatible packages"
        ),
        "from_code": getattr(selected, "from_code", None),
        "from_name": getattr(selected, "from_name", None),
        "to_code": getattr(selected, "to_code", None),
        "to_name": getattr(selected, "to_name", None),
        "package_version": getattr(selected, "package_version", None),
        "argos_version": getattr(selected, "argos_version", None),
        "links": safe_json(getattr(selected, "links", None)),
        "saved_model_file": str(target_path),
        "sha256": sha256,
    }

    (model_dir / "model_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (model_dir / "SHA256SUMS.txt").write_text(
        f"{sha256}  {target_path.name}\n",
        encoding="utf-8",
    )

    return target_path


def ensure_argos_model(
    from_code: str,
    to_code: str,
    model_dir: Path,
) -> Path:
    model_file = model_dir / f"translate-{from_code}_{to_code}.argosmodel"

    if model_is_suitable(model_file, from_code, to_code):
        sha256 = sha256_file(model_file)

        metadata_file = model_dir / "model_metadata.json"
        if not metadata_file.exists():
            metadata = {
                "selection_rule": "existing suitable local Argos-compatible model",
                "from_code": from_code,
                "to_code": to_code,
                "saved_model_file": str(model_file),
                "sha256": sha256,
                "package_metadata": read_argosmodel_metadata(model_file),
            }

            metadata_file.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        sha_file = model_dir / "SHA256SUMS.txt"
        if not sha_file.exists():
            sha_file.write_text(
                f"{sha256}  {model_file.name}\n",
                encoding="utf-8",
            )

        return model_file

    return download_best_argos_model(
        from_code=from_code,
        to_code=to_code,
        model_dir=model_dir,
    )