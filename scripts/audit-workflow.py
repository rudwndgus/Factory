"""Apply queue migrations and print a secret-free workflow integrity summary."""

import json
from collections import Counter

from factory import database as db


def active_topics():
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT office_id,payload FROM jobs "
            "WHERE status IN ('QUEUED','PLANNED','RUNNING','RETRYING','WAITING')"
        ).fetchall()
    return [
        (row["office_id"], json.loads(row["payload"] or "{}").get("topic_id"))
        for row in rows
    ]


before = active_topics()
duplicate_count = sum(
    count - 1
    for count in Counter(item for item in before if item[1]).values()
    if count > 1
)
db.migrate()
with db.connection() as connection:
    summary = {
        "active_duplicates_before": duplicate_count,
        "active_topic_claims": connection.execute(
            "SELECT count(*) FROM active_topic_jobs"
        ).fetchone()[0],
        "duplicates_cleaned": connection.execute(
            "SELECT count(*) FROM jobs WHERE error=?",
            ("Duplicate active topic queue entry removed during migration",),
        ).fetchone()[0],
        "completed_history_preserved": connection.execute(
            "SELECT count(*) FROM jobs WHERE status='COMPLETE'"
        ).fetchone()[0],
        "publish_slot_claims": connection.execute(
            "SELECT count(*) FROM publish_slot_claims"
        ).fetchone()[0],
    }
print(json.dumps(summary, ensure_ascii=False))
