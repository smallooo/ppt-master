"""Ad-hoc end-to-end smoke for the auth+DB-backed service. Not part of CI."""
from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from service.api.app import app

ADMIN = {"X-Admin-Token": os.environ.get("ADMIN_TOKEN", "")}


def _ok(r, expect=200):
    assert r.status_code == expect, f"{r.status_code} {r.text}"
    return r.json()


def main() -> None:
    c = TestClient(app)

    # 0a. unauthenticated mini call -> 401 (uniform error envelope)
    r = c.get("/api/v1/mini/projects/does-not-matter")
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["ok"] is False and body["error"]["code"] == "unauthorized", body
    print("unauth mini -> 401 (envelope ok)")

    # 0b. login (dev fallback when WECHAT_APPID is empty)
    body = _ok(c.post("/api/v1/auth/wechat/login",
                      json={"code": "user_alpha", "nickname": "Alpha"}))
    token = body["data"]["token"]
    me = body["data"]["user"]
    print("login ok user:", me["user_id"], "openid:", me["openid"])
    H = {"Authorization": f"Bearer {token}"}

    # 0c. /api/v1/mini/me
    body = _ok(c.get("/api/v1/mini/me", headers=H))
    assert body["data"]["user_id"] == me["user_id"]
    print("me ok")

    # 0d. templates (no auth required)
    body = _ok(c.get("/api/v1/mini/templates"))
    assert any(t["canvas_format"] == "ppt169" for t in body["data"])
    print("templates ->", len(body["data"]), "options")

    # 1. create
    body = _ok(c.post("/api/v1/mini/projects", headers=H,
                      json={"project_name": "auth_e2e", "canvas_format": "ppt169",
                            "requested_page_min": 3, "requested_page_max": 5}))
    pid = body["data"]["project_id"]
    print("created:", pid)

    # 1a. list mine
    body = _ok(c.get("/api/v1/mini/projects", headers=H))
    assert pid in [p["project_id"] for p in body["data"]]
    print("list mine ->", len(body["data"]))

    # 1b. patch
    body = _ok(c.patch(f"/api/v1/mini/projects/{pid}", headers=H,
                       json={"project_name": "auth_e2e_v2",
                             "requested_page_min": 4, "requested_page_max": 6}))
    assert body["data"]["project_name"] == "auth_e2e_v2"
    print("patched name + page range")

    # 1c. quota
    body = _ok(c.get("/api/v1/mini/quota", headers=H))
    assert body["data"]["project_count"] >= 1
    print("quota ->", body["data"]["project_count"], "projects")

    # 1d. foreign-user isolation
    r2 = c.post("/api/v1/auth/wechat/login", json={"code": "user_beta"})
    H2 = {"Authorization": f"Bearer {r2.json()['data']['token']}"}
    assert c.get(f"/api/v1/mini/projects/{pid}", headers=H2).status_code == 403
    assert c.get("/api/v1/mini/projects", headers=H2).json()["data"] == []
    print("foreign-user isolation ok")

    # 2. upload file
    md = "# Hello\n\nAuth e2e.\n\n## Detail\n\n- a\n- b\n"
    body = _ok(c.post(f"/api/v1/mini/projects/{pid}/sources", headers=H,
                      files={"file": ("intro.md", md.encode(), "text/markdown")},
                      data={"source_kind": "markdown", "role": "primary_source"}))
    src_id = body["data"]["source_file_id"]

    # 2a. list sources
    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/sources", headers=H))
    assert any(s["source_file_id"] == src_id for s in body["data"])

    # 2b. delete a temporary upload
    tmp = _ok(c.post(f"/api/v1/mini/projects/{pid}/sources", headers=H,
                     files={"file": ("scratch.md", b"# scratch\n", "text/markdown")},
                     data={"source_kind": "markdown", "role": "secondary_source"}))
    tmp_id = tmp["data"]["source_file_id"]
    _ok(c.delete(f"/api/v1/mini/projects/{pid}/sources/{tmp_id}", headers=H))
    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/sources", headers=H))
    assert all(s["source_file_id"] != tmp_id for s in body["data"])
    print("upload + list + delete source ok")

    # 3a. admin route blocked without token
    r = c.post(f"/api/v1/admin/projects/{pid}/confirmation/approve",
               json={"approved_by": "x"})
    assert r.status_code in (401, 503), r.status_code

    # 3b. finalize then user can fetch confirmation
    _ok(c.post(f"/api/v1/mini/projects/{pid}/sources/finalize", headers=H))
    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/confirmation", headers=H))
    assert body["data"]["status"] == "pending"
    print("user-side confirmation -> pending")

    # 3c. admin reject -> revised; then approve
    _ok(c.post(f"/api/v1/admin/projects/{pid}/confirmation/reject",
               headers=ADMIN, json={"reason": "need a cover slide"}))
    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/confirmation", headers=H))
    assert body["data"]["status"] == "revised"
    _ok(c.post(f"/api/v1/admin/projects/{pid}/confirmation/approve",
               headers=ADMIN, json={"approved_by": "auth_e2e"}))
    print("admin reject + approve ok")

    # 3d. admin lists
    body = _ok(c.get("/api/v1/admin/projects", headers=ADMIN))
    assert any(p["project_id"] == pid for p in body["data"])
    body = _ok(c.get("/api/v1/admin/users", headers=ADMIN))
    assert len(body["data"]) >= 1
    print("admin lists ok")

    # 4. generate
    _ok(c.post(f"/api/v1/mini/projects/{pid}/jobs/generate", headers=H))

    # 5. poll latest + list jobs/events
    job_id = None
    for _ in range(60):
        body = _ok(c.get(f"/api/v1/mini/projects/{pid}/jobs/latest", headers=H))
        d = body["data"]
        job_id = d["job_id"]
        if d["status"] in ("succeeded", "failed", "cancelled"):
            print("final job:", d["status"], d["current_stage"])
            break
        time.sleep(0.25)
    assert job_id

    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/jobs", headers=H))
    assert any(j["job_id"] == job_id for j in body["data"])
    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/jobs/{job_id}/events", headers=H))
    print("events recorded:", len(body["data"]))

    # 6. download primary pptx
    r = c.get(f"/api/v1/mini/projects/{pid}/download/pptx", headers=H)
    print("download pptx:", r.status_code, "bytes:", len(r.content))

    # 6a. download arbitrary artifact (design_spec)
    body = _ok(c.get(f"/api/v1/mini/projects/{pid}/artifacts", headers=H))
    spec = next(a for a in body["data"] if a["artifact_type"] == "design_spec")
    r = c.get(f"/api/v1/mini/projects/{pid}/download/{spec['artifact_id']}", headers=H)
    assert r.status_code == 200
    print("download artifact:", spec["artifact_type"], "bytes:", len(r.content))

    # 7. cancel-after-success -> 409
    r = c.post(f"/api/v1/mini/projects/{pid}/jobs/{job_id}/cancel", headers=H)
    assert r.status_code == 409, r.text
    print("cancel finished job -> 409 (expected)")

    # 8. logout invalidates the token
    _ok(c.post("/api/v1/auth/logout", headers=H))
    r = c.get("/api/v1/mini/me", headers=H)
    assert r.status_code == 401, r.text
    print("logout -> token revoked, /me -> 401")

    # 9. delete project (need a fresh login)
    body = _ok(c.post("/api/v1/auth/wechat/login", json={"code": "user_alpha"}))
    H3 = {"Authorization": f"Bearer {body['data']['token']}"}
    _ok(c.delete(f"/api/v1/mini/projects/{pid}", headers=H3))
    r = c.get(f"/api/v1/mini/projects/{pid}", headers=H3)
    assert r.status_code == 404
    print("project deleted")

    print("\nALL OK")


if __name__ == "__main__":
    main()
