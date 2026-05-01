from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_TO_MD_DIR = REPO_ROOT / "skills" / "ppt-master" / "scripts" / "source_to_md"

MARKDOWN_SUFFIXES = {".md", ".markdown", ".txt", ".csv", ".tsv"}
DOC_SUFFIXES = {
    ".docx", ".doc", ".odt", ".rtf", ".epub", ".html", ".htm", ".tex",
    ".latex", ".rst", ".org", ".ipynb", ".typ",
}
PRESENTATION_SUFFIXES = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
PDF_SUFFIXES = {".pdf"}


@lru_cache(maxsize=None)
def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_source_file(input_path: Path, output_path: Path) -> str:
    suffix = input_path.suffix.lower()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if suffix in MARKDOWN_SUFFIXES:
        shutil.copy2(input_path, output_path)
        return output_path.read_text(encoding="utf-8", errors="replace")

    if suffix in PDF_SUFFIXES:
        module = _load_module("ppt_master_pdf_to_md", SOURCE_TO_MD_DIR / "pdf_to_md.py")
        return module.extract_pdf_to_markdown(str(input_path), str(output_path))

    if suffix in DOC_SUFFIXES:
        module = _load_module("ppt_master_doc_to_md", SOURCE_TO_MD_DIR / "doc_to_md.py")
        return module.convert_to_markdown(str(input_path), str(output_path))

    if suffix in PRESENTATION_SUFFIXES:
        module = _load_module("ppt_master_ppt_to_md", SOURCE_TO_MD_DIR / "ppt_to_md.py")
        return module.convert_presentation_to_markdown(str(input_path), str(output_path))

    if suffix in EXCEL_SUFFIXES:
        module = _load_module("ppt_master_excel_to_md", SOURCE_TO_MD_DIR / "excel_to_md.py")
        return module.convert_to_markdown(str(input_path), str(output_path))

    raise ValueError(f"Unsupported source format for normalization: {suffix}")