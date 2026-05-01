"""Real OpenAI-driven generation pipeline.

Stages:
  1. preparing_context   — read normalized bundle + design_spec.md
  2. strategist          — LLM produces page outline JSON
  3. executor            — LLM produces an SVG per page
  4. finalize_svg        — run skill's svg post-processing
  5. svg_to_pptx         — run skill's PPTX exporter
  6. exported            — register pptx_main artifact

If anything fails, the caller is responsible for falling back to the
deterministic ``export_fallback_pptx_artifact`` path.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from service.adapters.openai_client import ChatMessage, OpenAIError, chat
from service.config import ServiceSettings
from service.core.workspace import WorkspaceManager


SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "skills" / "ppt-master" / "scripts"

ProgressFn = Callable[[str, int, str], None]
"""Callback ``(stage, percent, status_text)`` — workspace will persist it."""


@dataclass
class _PageSpec:
    page_no: int
    title: str
    key_points: list[str]
    layout_hint: str | None = None


class PipelineError(Exception):
    """Raised when the real pipeline cannot complete."""


# ----------------------------- Prompts ---------------------------------------


_STRATEGIST_SYSTEM = (
    "You are the Strategist of a PPT generation system. "
    "Given source material and a design spec, you decide the page structure. "
    "Reply ONLY with strict JSON: "
    '{"pages":[{"page_no":1,"title":"...","key_points":["..."],"layout_hint":"title|two_column|bullets|chart|closing"}]} '
    "Do not include commentary, markdown fences, or extra fields."
)

_EXECUTOR_SYSTEM = (
    "You are the Executor of a PPT generation system. "
    "You produce a single SVG page for a 1920x1080 canvas. "
    "Output ONLY the raw <svg>...</svg> string — no markdown fences, no prose, no XML prolog. "
    "The SVG MUST start with <svg width=\"1920\" height=\"1080\" viewBox=\"0 0 1920 1080\" xmlns=\"http://www.w3.org/2000/svg\">. "
    "Use only inline fills/strokes/fonts (system fonts). No external assets, no <image href=...> URLs."
)


# --------------------------- Public entry ------------------------------------


def run_real_pipeline(
    workspace: WorkspaceManager,
    project_id: str,
    job_id: str,
    *,
    progress: ProgressFn,
) -> Path:
    """Run the full LLM pipeline. Return the produced PPTX path."""
    settings = workspace.settings
    if not settings.openai_api_key:
        raise PipelineError("OPENAI_API_KEY not configured")

    project_root = settings.projects_root / project_id
    bundle_path = project_root / "normalized" / "normalized_sources.md"
    design_spec_path = project_root / "design_spec.md"
    if not bundle_path.exists() or not design_spec_path.exists():
        raise PipelineError("Generation context not materialized")

    progress("preparing_context", 25, "Loaded source bundle and design spec.")

    source_text = bundle_path.read_text(encoding="utf-8")
    design_spec = design_spec_path.read_text(encoding="utf-8")

    progress("strategist", 35, "Strategist deciding page structure.")
    pages = _run_strategist(settings, source_text, design_spec)

    progress("executor", 50, f"Executor generating {len(pages)} SVG pages.")
    svg_dir = project_root / "svg_output"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _clear_directory(svg_dir, suffix=".svg")
    for idx, page in enumerate(pages, start=1):
        svg = _run_executor(settings, page, design_spec)
        (svg_dir / f"page_{idx:02d}.svg").write_text(svg, encoding="utf-8")
        pct = 50 + int(30 * idx / max(1, len(pages)))
        progress("executor", pct, f"Executor wrote page {idx}/{len(pages)}.")

    progress("finalize_svg", 85, "Running SVG post-processing.")
    _run_finalize_svg(project_root)

    progress("svg_to_pptx", 92, "Exporting PPTX.")
    project_record = workspace.get_project(project_id)
    pptx_path = _run_svg_to_pptx(project_root, project_record.canvas_format)

    progress("exported", 98, "Registering PPTX artifact.")
    return pptx_path


# --------------------------- Internals ---------------------------------------


def _clear_directory(directory: Path, *, suffix: str) -> None:
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix == suffix:
            entry.unlink()


def _run_strategist(
    settings: ServiceSettings,
    source_text: str,
    design_spec: str,
) -> list[_PageSpec]:
    user_prompt = (
        "DESIGN SPEC (markdown):\n"
        f"{design_spec[:8000]}\n\n"
        "SOURCE MATERIAL (markdown, may be truncated):\n"
        f"{source_text[:16000]}\n\n"
        "Plan 6 to 12 pages covering the source. Return JSON now."
    )
    try:
        raw = chat(
            settings,
            model=settings.openai_model_strategist,
            messages=[
                ChatMessage("system", _STRATEGIST_SYSTEM),
                ChatMessage("user", user_prompt),
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        raise PipelineError(f"Strategist call failed: {exc}") from exc

    try:
        payload = json.loads(raw)
        items = payload["pages"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PipelineError(f"Strategist returned invalid JSON: {raw[:300]}") from exc

    pages: list[_PageSpec] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        pages.append(
            _PageSpec(
                page_no=int(item.get("page_no") or idx),
                title=str(item.get("title") or f"Page {idx}").strip(),
                key_points=[str(k) for k in (item.get("key_points") or []) if str(k).strip()],
                layout_hint=(str(item["layout_hint"]).strip() if item.get("layout_hint") else None),
            )
        )
    if not pages:
        raise PipelineError("Strategist returned no pages")
    return pages


_SVG_TAG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)


def _run_executor(
    settings: ServiceSettings,
    page: _PageSpec,
    design_spec: str,
) -> str:
    user_prompt = (
        f"DESIGN SPEC (markdown, follow the visual rules):\n{design_spec[:6000]}\n\n"
        f"PAGE NUMBER: {page.page_no}\n"
        f"PAGE TITLE: {page.title}\n"
        f"LAYOUT HINT: {page.layout_hint or 'auto'}\n"
        f"KEY POINTS:\n- " + "\n- ".join(page.key_points or ["(none)"]) + "\n\n"
        "Produce the SVG now."
    )
    try:
        raw = chat(
            settings,
            model=settings.openai_model_executor,
            messages=[
                ChatMessage("system", _EXECUTOR_SYSTEM),
                ChatMessage("user", user_prompt),
            ],
            temperature=0.5,
        )
    except OpenAIError as exc:
        raise PipelineError(
            f"Executor call failed on page {page.page_no}: {exc}"
        ) from exc

    match = _SVG_TAG_RE.search(raw)
    if not match:
        raise PipelineError(
            f"Executor for page {page.page_no} did not return an <svg> element"
        )
    return match.group(0)


def _run_finalize_svg(project_root: Path) -> None:
    cmd = [sys.executable, str(SKILL_SCRIPTS_DIR / "finalize_svg.py"), str(project_root)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PipelineError(
            f"finalize_svg failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )


def _run_svg_to_pptx(project_root: Path, canvas_format: str) -> Path:
    exports_dir = project_root / "exports"
    before = {p.name for p in exports_dir.glob("*.pptx")} if exports_dir.exists() else set()
    cmd = [
        sys.executable,
        str(SKILL_SCRIPTS_DIR / "svg_to_pptx.py"),
        str(project_root),
        "-s",
        "final",
        "--format",
        canvas_format or "ppt169",
        "--only",
        "native",
        "--quiet",
        "--no-notes",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PipelineError(
            f"svg_to_pptx failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    after = {p for p in exports_dir.glob("*.pptx")}
    new = [p for p in after if p.name not in before]
    if not new:
        raise PipelineError("svg_to_pptx did not produce a new PPTX")
    new.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return new[0]
