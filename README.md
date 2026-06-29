# Argos Batch Translation

This project batch-translates `.txt` files with Argos Translate.

It is designed for a simple workflow:

1. Put source `.txt` files in `corpora/raw/`
2. Run one Python script
3. The script checks whether a suitable Argos model is already available
4. If no suitable model is found, it downloads one
5. It translates all `.txt` files into `corpora/translated/`
6. It writes logs and metadata to `logs/` and `models/`

The current default configuration translates:

```text
Dutch → English
nl → en
```

---

## Installation

### 1. Install Python

Use Python 3.10 or newer.

Check your Python version:

```powershell
python --version
```

### 2. Install `uv`

If `uv` is not installed yet:

```powershell
pip install uv
```

Check that it works:

```powershell
uv --version
```

### 3. Create a virtual environment

From the project folder:

```powershell
uv venv
```

Activate it:

```powershell
.venv\Scripts\activate
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\activate
```

### 4. Install dependencies

```powershell
uv pip install -r requirements.txt
```

## Usage

### 1. Add source files

Place your Dutch `.txt` files in:

```text
corpora/raw/
```

Example:

```text
corpora/raw/interview_01.txt
corpora/raw/interview_02.txt
...
```

### 2. Run the translation script

```powershell
python translate_batch.py
```

The script will:

* download a suitable model if it is missing
* install the local model into Argos
* translate every `.txt` file in `corpora/raw/`
* write translated files to `corpora/translated/`

Example output files:

```text
corpora/translated/interview_01.en.txt
corpora/translated/interview_02.en.txt
...
```

### 3. Change the language pair

Open `translate_batch.py` and change:

```python
SOURCE_LANG = "nl"
TARGET_LANG = "en"
```

For example, German to English:

```python
SOURCE_LANG = "de"
TARGET_LANG = "en"
```

### 4. Optional environment settings

For a CPU-only run:

```powershell
$env:ARGOS_DEVICE_TYPE = "cpu"
```

---

## Traceability Features

The project records information about the model, input files, output files, software environment, and translation run.

## Model archiving

If the required model is missing, the script downloads an Argos-compatible model and saves it locally:

```text
models/translate-nl_en.argosmodel
```

The model is then reused from the local `models/` folder in later runs. The model metadata is recorded upon downloading it in

```text
models/model_metadata.json
models/SHA256SUMS.txt
```

These files record:

* source language
* target language
* package version
* Argos version
* model file path
* model SHA-256 checksum
* selection rule used for choosing the model

### Translation manifest

Each translation run writes:

```text
logs/translation_manifest.json
```

This describes the overall translation run, including:

* creation timestamp
* source language
* target language
* input directory
* output directory
* model file
* model checksum
* Python version
* platform information
* installed package versions
* relevant environment variables

### Translation ledger

Each run writes:

```text
logs/translation_ledger.jsonl
```

This is a line-by-line audit log. Each line corresponds to one translated input document.

For every document, it records:

* input file path
* output file path
* source language
* target language
* model file
* model checksum
* timestamp
* input encoding
* input file SHA-256 checksum
* normalized source text SHA-256 checksum
* output file SHA-256 checksum
* source character count
* translation character count
* status
* error message, if any

### Error log

Failed translations are written to:

```text
logs/errors.jsonl
```

This makes it possible to inspect failed files separately without searching through the full ledger.

### File hashing

The project uses SHA-256 hashes for:

* the model file
* each raw input file
* each normalized source text
* each translated output file

This makes it possible to check whether any input, output, or model file changed after translation.
