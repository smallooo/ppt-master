"""P3 smoke: run the real LLM pipeline with the OpenAI client monkey-patched.

Validates that ``service.workers.pipeline.run_real_pipeline`` correctly drives
the skill scripts (``finalize_svg.py`` and ``svg_to_pptx.py``) end-to-end and
produces a real DrawingML PPTX.

Run via::

    DATABASE_URL=... SESSION_SECRET=... ADMIN_TOKEN=devadmin \\
        PYTHONPATH=. python scripts/p3_pipeline_smoke.py
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

# Ensure the worker thinks OpenAI is configured so the real pipeline path runs.
os.environ.setdefault("OPENAI_API_KEY", "sk-mock-for-smoke")

# Patch the LLM call BEFORE the worker thread imports it.
import service.adapters.openai_client as oc

_PAGES = [
    {
        "title": "Pipeline Smoke",
        "key_points": ["Real finalize_svg", "Real svg_to_pptx", "DrawingML output"],
    },
    {
        "title": "Status",
        "key_points": ["Strategist mocked", "Executor mocked", "Skill scripts real"],
    },
]


def _mock_chat(settings, *, model, messages, temperature=0.4, response_format=None, timeout=120.0):  # noqa: D401, ARG001
    """Return canned strategist JSON or canned executor SVG."""
    sys_msg = next((m.content for m in messages if m.role == "system"), "")
    if "Strategist" in sys_msg:
        return json.dumps(
            {"pages": [{"page_no": i + 1, **p, "layout_hint": "bullets"}
                       for i, p in enumerate(_PAGES)]}
        )
    # Executor — extract the page title from the user prompt for a tiny SVG.
    user_msg = next((m.content for m in messages if m.role == "user"), "")
    title = "Page"
    for line in user_msg.splitlines():
        if line.startswith("PAGE TITLE:"):
            title = line.split(":", 1)[1].strip() or title
            break
    return (
        '<svg width="1920" height="1080" viewBox="0 0 1920 1080" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="0" y="0" width="1920" height="1080" fill="#0f172a"/>'
        f'<text x="120" y="200" font-family="Helvetica" font-size="84" fill="#f8fafc">{title}</text>'
        '<text x="120" y="320" font-family="Helvetica" font-size="40" fill="#94a3b8">'
        'PPT Master pipeline smoke</text>'
        '</svg>'
    )


oc.chat = _mock_chat  # type: ignore[assignment]

# pipeline.py did `from ... import chat`, so re-bind its module-level reference too.
import service.workers.pipeline as wp  # noqa: E402

wp.chat = _mock_chat  # type: ignore[assignment]

# Importing the app AFTER patching ensures the worker uses the patched chat.
from service.api.app import app  # noqa: E402


def main() -> None:
    c = TestClient(app)

    # Login
    r = c.post("/api/v1/auth/wechat/login", json={"code": "p3_user"})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['data']['token']}"}

    # Create project
    r = c.post("/api/v1/mini/projects", headers=H,
               json={"project_name": "p3_smoke", "canvas_format": "ppt169",
                     "requested_page_min": 2, "requested_page_max": 3})
    pid = r.json()["data"]["project_id"]
    print("created:", pid)

    # Upload + finalize
    md = "# P3\n\nReal pipeline mocked LLM.\n\n## Why\n\n- A\n- B\n"
    c.post(f"/api/v1/mini/projects/{pid}/sources", headers=H,
           files={"file": ("intro.md", md.encode(), "text/markdown")},
           data={"source_kind": "markdown", "role": "primary_source"})
    c.post(f"/api/v1/mini/projects/{pid}/sources/finalize", headers=H)

    # Generate
    c.post(f"/api/v1/mini/projects/{pid}/jobs/generate", headers=H)

    last_status = None
    for _ in range(240):  # finalize+pptx may take some seconds
        r = c.get(f"/api/v1/mini/projects/{pid}/jobs/latest", headers=H)
        d = r.json()["data"]
        if d["status"] != last_status or d["current_stage"] not in ("queued", last_status):
            print(f"  stage={d['current_stage']:18s} status={d['status']:9s} pct={d['progress_percent']}")
            last_status = d["status"]
        if d["status"] in ("succeeded", "failed"):
            assert d["status"] == "succeeded", d
            assert d["current_stage"] == "exported", d
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Pipeline did not finish in time")

    # Verify the produced PPTX is real DrawingML, not the fallback python-pptx output.
    r = c.get(f"/api/v1/mini/projects/{pid}/download/pptx", headers=H)
    assert r.status_code == 200
    pptx_bytes = r.content
    print("pptx bytes:", len(pptx_bytes))

    cd = r.headers.get("content-disposition", "")
    assert "_fallback.pptx" not in cd, f"got fallback pptx: {cd}"
    print("filename:", cd)

    # Inspect slides for the executor's text marker
    tmp = Path(".tmp_p3.pptx")
    tmp.write_bytes(pptx_bytes)
    found_marker = False
    with zipfile.ZipFile(tmp) as z:
        slide_names = sorted(n for n in z.namelist() if n.startswith("ppt/slides/slide"))
        print("slides:", len(slide_names), slide_names[:3])
        for name in slide_names:
            xml = z.read(name).decode("utf-8", errors="ignore")
            if "PPT Master pipeline smoke" in xml or "Pipeline Smoke" in xml:
                found_marker = True
                break
    tmp.unlink(missing_ok=True)
    assert found_marker, "Executor SVG content not found in any slide XML"
    print("OK: real pipeline produced DrawingML slides containing executor text.")


if __name__ == "__main__":
    main()
