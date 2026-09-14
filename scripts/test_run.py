"""Explicit end-to-end offline TEST RUN, with real speech/MP4. No uploads."""

import json
import time
from factory import database as db, engine, media
from factory.config import MEDIA

db.migrate()
office = db.create_office("Acceptance TEST RUN", {})
id = engine.enqueue(office["id"], True)
for _ in range(10):
    engine.tick()
    with db.connection() as c:
        job = dict(c.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone())
    print(job["stage"], job["status"], flush=True)
    if job["status"] in ("FAILED", "RETRYING", "BLOCKED"):
        raise RuntimeError(job["error"])
    if job["status"] == "COMPLETE":
        break
assert job["status"] == "COMPLETE"
video = next(r["data"] for r in db.rows("videos", office["id"]) if r["id"] == id)
assert video["test_mode"] and video["status"] == "TEST_COMPLETE"
assert all(value for key, value in video["qc"].items() if key != "not_test"), video[
    "qc"
]
assert not db.rows("uploads", office["id"])
print(
    json.dumps(
        {
            "file": str(MEDIA / video["file"]),
            "duration": video["actual_duration"],
            "qc": video["qc"],
        }
    ),
    flush=True,
)
