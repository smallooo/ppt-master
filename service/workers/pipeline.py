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

_IMAGE_PROVIDER_ERROR_MARKERS = (
    "ModelNotOpen",
    "AuthenticationError",
    "invalid_api_key",
    "invalid api key",
    "not activated the model",
    "Insufficient Balance",
    "Payment Required",
)

ProgressFn = Callable[[str, int, str], None]
"""Callback ``(stage, percent, status_text)`` — workspace will persist it."""


@dataclass
class _PageSpec:
    page_no: int
    title: str
    key_points: list[str]
    layout_hint: str | None = None
    narrative_role: str | None = None
    layout_strategy: str | None = None
    visual_focus: str | None = None
    image_intent: str | None = None
    visualization_hint: str | None = None


@dataclass
class _ImageSpec:
    filename: str
    dimensions: str
    aspect_ratio: str
    purpose: str
    image_type: str
    status: str
    description: str


@dataclass
class _ImageGenerationResult:
    generated_files: list[str]
    warning: str | None = None


class PipelineError(Exception):
    """Raised when the real pipeline cannot complete."""


# ----------------------------- Prompts ---------------------------------------


_STRATEGIST_SYSTEM = (
    "You are the Strategist of a PPT generation system. "
    "Given source material and a design spec, you decide the page structure and narrative rhythm. "
    "Reply ONLY with strict JSON: "
    '{"pages":[{"page_no":1,"title":"...","key_points":["..."],"layout_hint":"cover|editorial|comparison|timeline|framework|data_story|quote|closing","narrative_role":"anchor|dense|breathing","layout_strategy":"...","visual_focus":"...","image_intent":"hero|atmosphere|side_by_side|accent|none","visualization_hint":"..."}]} '
    "Do not include commentary, markdown fences, or extra fields."
)

_EXECUTOR_SYSTEM = (
    "You are the Executor of a PPT generation system. "
    "You produce a single SVG page for a 1920x1080 canvas. "
    "Output ONLY the raw <svg>...</svg> string — no markdown fences, no prose, no XML prolog. "
    "The SVG MUST start with <svg width=\"1920\" height=\"1080\" viewBox=\"0 0 1920 1080\" xmlns=\"http://www.w3.org/2000/svg\">. "
    "Use only inline fills/strokes/fonts (system fonts). "
    "Project-local images are allowed with <image href=\"../images/<filename>\" .../> when they are listed as available assets. "
    "Do not use any remote URLs or external network assets. "
    "Avoid repeating the same card-grid composition on consecutive pages. "
    "For breathing pages, prefer one dominant idea with generous whitespace and no repeated multi-card layout."
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
    spec_lock_path = project_root / "spec_lock.md"
    if not bundle_path.exists() or not design_spec_path.exists() or not spec_lock_path.exists():
        raise PipelineError("Generation context not materialized")

    progress("preparing_context", 25, "Loaded source bundle and design spec.")

    source_text = bundle_path.read_text(encoding="utf-8")
    design_spec = design_spec_path.read_text(encoding="utf-8")
    spec_lock = spec_lock_path.read_text(encoding="utf-8")

    progress("strategist", 35, "Strategist deciding page structure.")
    pages = _run_strategist(settings, source_text, design_spec)

    project_record = workspace.get_project(project_id)
    progress("image_generation", 45, "Preparing image prompts and assets.")
    image_result = _run_image_generation(project_root, project_record.project_name, design_spec, pages)
    image_assets = image_result.generated_files
    if image_result.warning:
        progress("image_generation", 48, image_result.warning)

    progress("executor", 50, f"Executor generating {len(pages)} SVG pages.")
    svg_dir = project_root / "svg_output"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _clear_directory(svg_dir, suffix=".svg")
    for idx, page in enumerate(pages, start=1):
        svg = _run_executor(
            settings,
            page,
            design_spec,
            spec_lock=spec_lock,
            available_images=image_assets,
            is_cover_page=(idx == 1),
        )
        (svg_dir / f"page_{idx:02d}.svg").write_text(svg, encoding="utf-8")
        pct = 50 + int(30 * idx / max(1, len(pages)))
        progress("executor", pct, f"Executor wrote page {idx}/{len(pages)}.")

    progress("finalize_svg", 85, "Running SVG post-processing.")
    _run_finalize_svg(project_root)

    progress("svg_to_pptx", 92, "Exporting PPTX.")
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
        "Plan a deck whose page count stays inside the requested range. "
        "Vary page rhythm across anchor, dense, and breathing pages. "
        "Do not repeat the same layout family more than twice in a row. Return JSON now."
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
                narrative_role=(str(item["narrative_role"]).strip() if item.get("narrative_role") else None),
                layout_strategy=(str(item["layout_strategy"]).strip() if item.get("layout_strategy") else None),
                visual_focus=(str(item["visual_focus"]).strip() if item.get("visual_focus") else None),
                image_intent=(str(item["image_intent"]).strip() if item.get("image_intent") else None),
                visualization_hint=(str(item["visualization_hint"]).strip() if item.get("visualization_hint") else None),
            )
        )
    if not pages:
        raise PipelineError("Strategist returned no pages")
    return pages


_SVG_TAG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)


def _run_image_generation(
    project_root: Path,
    project_name: str,
    design_spec: str,
    pages: list[_PageSpec],
) -> _ImageGenerationResult:
    image_specs = _extract_image_specs(design_spec)
    if not image_specs and pages:
        image_specs = [
            _ImageSpec(
                filename="cover_bg.png",
                dimensions="1920x1080",
                aspect_ratio="16:9",
                purpose="封面背景图",
                image_type="Background",
                status="Pending",
                description=(
                    f"为《{project_name}》第一页《{pages[0].title}》生成专业封面背景，"
                    "适合演示文稿标题覆盖，保留中心留白。"
                ),
            )
        ]

    if not image_specs:
        return _ImageGenerationResult(generated_files=[])

    images_dir = project_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    prompt_doc = _build_image_prompt_document(project_name, image_specs)
    (images_dir / "image_prompts.md").write_text(prompt_doc, encoding="utf-8")

    generated_files: list[str] = []
    warning: str | None = None
    for image in image_specs:
        target_path = images_dir / image.filename
        existing_name = _find_generated_image_name(images_dir, image.filename)
        if existing_name is not None:
            generated_files.append(existing_name)
            continue
        if image.status.lower() != "pending":
            continue

        cmd = [
            sys.executable,
            str(SKILL_SCRIPTS_DIR / "image_gen.py"),
            image.description,
            "--backend",
            "volcengine",
            "--aspect_ratio",
            image.aspect_ratio,
            "--image_size",
            "1K",
            "--output",
            str(images_dir),
            "--filename",
            image.filename,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:
            warning = f"Image generation unavailable, continuing without images: {exc}"
            break

        generated_name = _find_generated_image_name(images_dir, image.filename)
        if proc.returncode == 0 and generated_name is not None:
            generated_files.append(generated_name)
            continue

        failure_text = proc.stderr.strip() or proc.stdout.strip() or f"image_gen exited with {proc.returncode}"
        if _is_image_provider_unavailable(failure_text):
            warning = (
                "Image generation unavailable, continuing without images: "
                f"{_summarize_image_failure(failure_text)}"
            )
            break

        if warning is None:
            warning = (
                "Some images could not be generated; continuing without missing assets: "
                f"{_summarize_image_failure(failure_text)}"
            )

    return _ImageGenerationResult(generated_files=generated_files, warning=warning)


def _find_generated_image_name(images_dir: Path, requested_name: str) -> str | None:
    direct_path = images_dir / requested_name
    if direct_path.exists():
        return requested_name

    stem = Path(requested_name).stem
    matches = sorted(path.name for path in images_dir.glob(f"{stem}.*") if path.is_file())
    return matches[0] if matches else None


def _is_image_provider_unavailable(message: str) -> bool:
    normalized = message.lower()
    return any(marker.lower() in normalized for marker in _IMAGE_PROVIDER_ERROR_MARKERS)


def _summarize_image_failure(message: str, *, limit: int = 180) -> str:
    compact = " ".join(message.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _extract_image_specs(design_spec: str) -> list[_ImageSpec]:
    lines = design_spec.splitlines()
    in_section = False
    specs: list[_ImageSpec] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## "):
            if in_section:
                break
            in_section = "Image Resource List" in line
            continue
        if not in_section or not line.startswith("|"):
            continue
        if "Filename" in line or line.startswith("| --------"):
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if len(columns) < 8:
            continue
        specs.append(
            _ImageSpec(
                filename=columns[0],
                dimensions=columns[1],
                aspect_ratio=columns[2],
                purpose=columns[3],
                image_type=columns[5],
                status=columns[6],
                description=columns[7],
            )
        )
    return specs


def _build_image_prompt_document(project_name: str, image_specs: list[_ImageSpec]) -> str:
    lines = [
        f"# Image Prompts - {project_name}",
        "",
        "Deck Style Anchor:",
        "professional presentation visual, clean composition, subtle contrast, generous negative space for text overlay, high quality",
        "",
    ]
    for idx, image in enumerate(image_specs, start=1):
        lines.extend([
            f"### Image {idx}: {image.filename}",
            "",
            "| Attribute | Value |",
            "| --------- | ----- |",
            f"| Purpose | {image.purpose} |",
            f"| Type | {image.image_type} |",
            f"| Dimensions | {image.dimensions} ({image.aspect_ratio}) |",
            f"| Original description | {image.description} |",
            "",
            "**Prompt**:",
            f"Deck Style Anchor, {image.description}",
            "",
            "**Negative Prompt**:",
            "text, watermark, logo, blurry details, cluttered layout, distorted objects",
            "",
            "**Alt Text**:",
            f"> {image.purpose}: {image.description}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _run_executor(
    settings: ServiceSettings,
    page: _PageSpec,
    design_spec: str,
    *,
    spec_lock: str,
    available_images: list[str],
    is_cover_page: bool,
) -> str:
    cover_image_name = next(
        (name for name in available_images if Path(name).stem == "cover_bg"),
        None,
    )
    image_guidance_lines = [
        "AVAILABLE IMAGE ASSETS:",
        *(f"- {name}" for name in available_images),
        "",
    ] if available_images else ["AVAILABLE IMAGE ASSETS:\n- (none)\n"]

    cover_guidance = (
        f"If {cover_image_name} is available and this is the first page, use it as a full-bleed background "
        f"with <image href=\"../images/{cover_image_name}\" x=\"0\" y=\"0\" width=\"1920\" height=\"1080\" preserveAspectRatio=\"xMidYMid slice\"/>."
        if is_cover_page and cover_image_name else
        "Use listed project-local images when they clearly improve the page; otherwise build the page without images."
    )
    user_prompt = (
        f"DESIGN SPEC (markdown, follow the visual rules):\n{design_spec[:6000]}\n\n"
        f"SPEC LOCK (json markdown, use as execution source of truth):\n{spec_lock[:5000]}\n\n"
        f"PAGE NUMBER: {page.page_no}\n"
        f"PAGE TITLE: {page.title}\n"
        f"LAYOUT HINT: {page.layout_hint or 'auto'}\n"
        f"NARRATIVE ROLE: {page.narrative_role or 'dense'}\n"
        f"LAYOUT STRATEGY: {page.layout_strategy or 'Use a composition that differs from nearby pages.'}\n"
        f"VISUAL FOCUS: {page.visual_focus or 'Highlight one clear page-level message.'}\n"
        f"IMAGE INTENT: {page.image_intent or 'none'}\n"
        f"VISUALIZATION HINT: {page.visualization_hint or 'none'}\n"
        f"KEY POINTS:\n- " + "\n- ".join(page.key_points or ["(none)"]) + "\n\n"
        + "\n".join(image_guidance_lines)
        + f"{cover_guidance}\n\n"
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
