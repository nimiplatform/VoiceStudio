import os, contextlib, sqlite3, logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.config import OUTPUTS_DIR
from core.db import db_conn, ensure_schema
from core import event_bus
router = APIRouter()
logger = logging.getLogger("voicestudio.history")


@router.get('/stats/usage')
def stats_usage():
    from services.local_stats import usage_summary
    return usage_summary()

def _safe_output_path(name):
    if not name:
        return None
    base = os.path.basename(name)
    if base != name:
        return None
    outputs_real = os.path.realpath(OUTPUTS_DIR)
    candidate = os.path.realpath(os.path.join(OUTPUTS_DIR, base))
    if not candidate.startswith(outputs_real + os.sep):
        return None
    return candidate


def _remove_wav_if_unreferenced(conn, audio_path, exclude_ids=()):
    """Delete a history WAV from OUTPUTS_DIR — but only when no *other*
    generation_history row still references the same file.

    History WAVs are uniquely owned by their row (lock/save-as-profile COPY
    into VOICES_DIR, exports copy to the user's destination), so this guard is
    normally a no-op — it exists so any future path that duplicates a row can
    never make a delete/prune yank audio out from under a surviving take."""
    if not audio_path:
        return
    p = _safe_output_path(audio_path)
    if not p or not os.path.exists(p):
        return
    placeholders = ",".join("?" for _ in exclude_ids)
    others = conn.execute(
        "SELECT COUNT(*) FROM generation_history WHERE audio_path=?"
        + (f" AND id NOT IN ({placeholders})" if exclude_ids else ""),
        (audio_path, *exclude_ids),
    ).fetchone()[0]
    if others:
        return
    with contextlib.suppress(OSError):
        os.remove(p)


# How many takes to keep before pruning the oldest UNstarred ones (rows + their
# WAVs). User-tunable via Settings → Storage; 0 = unlimited. The pref key is
# shared with api/routers/settings.py (the GET/PUT endpoint) — same pattern as
# perf.torch_compile_disabled, which settings.py and engine_env.py both name.
HISTORY_CAP_PREF_KEY = "generation_history_cap"
DEFAULT_HISTORY_CAP = 200


def _history_cap() -> int:
    from core import prefs

    try:
        cap = int(prefs.get(HISTORY_CAP_PREF_KEY, DEFAULT_HISTORY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_CAP
    return max(0, cap)


def _prune_history_over_cap() -> int:
    """Retention: keep the newest ``_history_cap()`` takes; delete the oldest
    UNstarred rows over the cap plus their WAVs (via the unreferenced guard).
    Starred takes are never pruned — even when they alone exceed the cap.
    Returns the number of rows pruned."""
    cap = _history_cap()
    if cap <= 0:
        return 0  # 0 = unlimited
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
        excess = total - cap
        if excess <= 0:
            return 0
        victims = conn.execute(
            "SELECT id, audio_path FROM generation_history "
            "WHERE COALESCE(starred, 0)=0 ORDER BY created_at ASC LIMIT ?",
            (excess,),
        ).fetchall()
        if not victims:
            return 0
        victim_ids = [r["id"] for r in victims]
        conn.executemany(
            "DELETE FROM generation_history WHERE id=?", [(i,) for i in victim_ids]
        )
        for r in victims:
            _remove_wav_if_unreferenced(conn, r["audio_path"], exclude_ids=victim_ids)
        logger.info("history retention: pruned %d takes over the %d cap", len(victims), cap)
        return len(victims)


@router.get("/history")
def list_history():
    """The newest 50 generations plus every starred take, newest first, kept to
    rows whose audio still exists on disk.

    Starred takes ride along past the 50-row window so a keeper can never age
    off the rail. Rows whose WAV was deleted out-of-band (cleared outputs dir,
    manual cleanup) used to come back anyway and render dead players that 404
    on every fetch; prune them here so the UI never sees them again."""
    query = (
        "SELECT * FROM generation_history WHERE COALESCE(starred, 0)=1 "
        "OR id IN (SELECT id FROM generation_history ORDER BY created_at DESC LIMIT 50) "
        "ORDER BY created_at DESC"
    )
    with db_conn() as conn:
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.OperationalError:
            # Same class as #710/#552: a DB that missed init or the additive
            # `starred` column. Heal once and retry inside this connection.
            ensure_schema()
            rows = conn.execute(query).fetchall()
        alive, stale_ids = [], []
        for r in rows:
            p = _safe_output_path(r["audio_path"]) if r["audio_path"] else None
            if r["audio_path"] and (not p or not os.path.exists(p)):
                stale_ids.append(r["id"])
            else:
                alive.append(dict(r))
        if stale_ids:
            conn.executemany(
                "DELETE FROM generation_history WHERE id=?",
                [(i,) for i in stale_ids],
            )
            logger.info("pruned %d stale history rows (audio file gone)", len(stale_ids))
    return alive


class _StarBody(BaseModel):
    starred: bool


@router.put("/history/{history_id}/starred")
def set_history_starred(history_id: str, body: _StarBody):
    """Star/unstar a take. Starred takes survive the retention cap and always
    appear in GET /history regardless of the recency window."""
    def _update():
        with db_conn() as conn:
            cur = conn.execute(
                "UPDATE generation_history SET starred=? WHERE id=?",
                (1 if body.starred else 0, history_id),
            )
            return cur.rowcount

    try:
        changed = _update()
    except sqlite3.OperationalError as e:
        # `no such column: starred` on a pre-migration DB (or the #710
        # missing-table class) — heal the schema and retry once.
        logger.warning("star update failed (%s); healing schema + retrying", e)
        ensure_schema()
        changed = _update()
    if not changed:
        raise HTTPException(
            status_code=404,
            detail="That take no longer exists — it may have been pruned or deleted.",
        )
    event_bus.emit("generation_history", {"action": "starred", "id": history_id})
    return {"id": history_id, "starred": body.starred}

@router.delete("/history")
def clear_history():
    with db_conn() as conn:
        rows = conn.execute("SELECT audio_path FROM generation_history").fetchall()
        for r in rows:
            p = _safe_output_path(r["audio_path"])
            if p and os.path.exists(p):
                with contextlib.suppress(OSError):
                    os.remove(p)
        conn.execute("DELETE FROM generation_history")
    event_bus.emit("generation_history")
    return {"cleared": True}

@router.delete("/history/{history_id}")
def delete_single_history(history_id: str):
    with db_conn() as conn:
        row = conn.execute("SELECT audio_path FROM generation_history WHERE id=?", (history_id,)).fetchone()
        conn.execute("DELETE FROM generation_history WHERE id=?", (history_id,))
        if row:
            # Row first, file second — the WAV goes only if no surviving take
            # still references it (see _remove_wav_if_unreferenced).
            _remove_wav_if_unreferenced(conn, row["audio_path"], exclude_ids=(history_id,))
    event_bus.emit("generation_history", {"action": "deleted", "id": history_id})
    return {"deleted": True}
