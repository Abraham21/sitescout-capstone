"""The operator's local SQLite record. Each completed stage is saved immediately."""

import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def runtime_dir():
    path = Path(os.environ.get("SITESCOUT_DATA_DIR", ROOT / ".runtime"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def connect():
    db = sqlite3.connect(runtime_dir() / "sitescout.sqlite", timeout=20)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize():
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, mode TEXT, started_at TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS leads (
                run_id TEXT, id TEXT, data TEXT, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, run_id TEXT, data TEXT);
            CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id,seq);
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY, run_id TEXT, lead_id TEXT, service TEXT,
                dollars REAL, state TEXT, input_tokens INTEGER, output_tokens INTEGER,
                created_at TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS contact_actions (
                lead_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('contacted','dismissed')), changed_at TEXT);
        """)
        if not db.execute("PRAGMA user_version").fetchone()[0]:
            db.execute("BEGIN IMMEDIATE")
            # Earlier desks stored dismissal inside the run. Keep the decision and restore the graph outcome.
            db.execute("""
                INSERT OR IGNORE INTO contact_actions(lead_id,run_id,state)
                SELECT l.id,l.run_id,'dismissed' FROM leads l JOIN runs r ON r.id=l.run_id
                WHERE r.mode IN ('live','showcase','recheck') AND l.data->>'$.status'='dismissed'
                ORDER BY r.started_at DESC,r.rowid DESC""")
            db.execute("""
                UPDATE leads SET data=json_set(data,'$.status',coalesce(data->>'$.original_status','hold'))
                WHERE data->>'$.status'='dismissed' AND run_id IN
                      (SELECT id FROM runs WHERE mode IN ('live','showcase','recheck'))""")
            db.execute("PRAGMA user_version=1")


def new_run(run_id, mode):
    with connect() as db:
        db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run_id, mode, now(), "running"))


def finish_run(run_id, status):
    with connect() as db:
        db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


def load_recorded_sample():
    """Install the bundled, synthetic recording once on a fresh operator desk."""
    sample = ROOT / "examples/recorded-run.json"
    if not sample.is_file():
        return
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Existing operator runs keep their own captured HTML. A sample-only
        # installation can also pick up a newer recording after a git pull.
        if db.execute("SELECT 1 FROM runs WHERE mode != 'sample' LIMIT 1").fetchone():
            return
        record = json.loads(sample.read_text())
        run = record["run"]
        if db.execute("SELECT 1 FROM runs WHERE id=?", (run["id"],)).fetchone():
            return
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?)", (run["id"], "sample", run["started_at"], "complete")
        )
        for lead in record["leads"]:
            preview = lead.get("preview")
            if preview:
                # Use the exact page the reviewer saw, regardless of later template changes.
                path = ROOT / "examples/pages" / (Path(lead["id"]).name + ".html.gz")
                page = gzip.decompress(path.read_bytes())
                if hashlib.sha256(page).hexdigest() != preview.pop("html_sha256"):
                    raise ValueError("The bundled preview does not match its recording.")
                preview["html"] = page.decode()
            db.execute("INSERT INTO leads VALUES (?,?,?)", (run["id"], lead["id"], json.dumps(lead)))
        db.executemany(
            "INSERT INTO events(run_id,data) VALUES (?,?)",
            [(run["id"], json.dumps(event)) for event in record["events"]],
        )


def list_runs():
    initialize()
    load_recorded_sample()
    with connect() as db:
        return [
            dict(row)
            for row in db.execute("""
            SELECT runs.*,sum(CASE WHEN json_extract(leads.data,'$.status')='ready' THEN 1 ELSE 0 END) AS ready_count,
                   sum(CASE WHEN json_extract(leads.data,'$.preview.published')=1 THEN 1 ELSE 0 END) AS published_count
            FROM runs LEFT JOIN leads ON leads.run_id=runs.id WHERE runs.status != 'superseded' GROUP BY runs.id ORDER BY runs.started_at DESC""")
        ]


def save_lead(run_id, lead):
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO leads VALUES (?,?,?)",
            (run_id, lead["id"], json.dumps(lead, ensure_ascii=False)),
        )


def leads(run_id):
    with connect() as db:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT data FROM leads WHERE run_id=? ORDER BY rowid", (run_id,))
        ]


def contact_queue(state="ready"):
    """Show current released drafts or the specific run the operator acted on."""
    with connect() as db:
        return [
            dict(row)
            for row in db.execute("""
            WITH ranked AS (
              SELECT l.run_id,l.id,l.data,r.started_at,
                     row_number() OVER (PARTITION BY l.id ORDER BY r.started_at DESC,r.rowid DESC) AS n
              FROM leads l JOIN runs r ON r.id=l.run_id
              WHERE r.mode IN ('live','showcase','recheck') AND (
                    r.status IN ('complete','complete_with_errors') OR
                    EXISTS (SELECT 1 FROM contact_actions a WHERE a.lead_id=l.id AND a.run_id=l.run_id))
            )
            SELECT id,ranked.run_id,started_at,actions.changed_at,data->>'$.name' AS name,data->>'$.city' AS city,
                   data->>'$.channel' AS channel,data->>'$.preview.url' AS preview_url,
                   coalesce(nullif(data->>'$.email',''),data->>'$.qualification.email') AS email,
                   data->>'$.qualification.social' AS social, data->>'$.qualification.website' AS website,
                   coalesce(nullif(data->>'$.qualification.phone',''),data->>'$.phone') AS phone
            FROM ranked LEFT JOIN contact_actions AS actions ON actions.lead_id=ranked.id
            WHERE (:state='ready' AND n=1 AND actions.lead_id IS NULL AND data->>'$.status' IN ('ready','manual')
                   AND data->>'$.preview.published'=1 AND data->>'$.preview.url'!='' AND data->>'$.pitch.body'!=''
                   AND coalesce(data->>'$.preview.content_verified',1)=1)
               OR (actions.state=:state AND actions.run_id=ranked.run_id)
            ORDER BY coalesce(actions.changed_at, started_at) DESC,name COLLATE NOCASE""", {"state": state})
        ]


def seen_live_ids():
    with connect() as db:
        return {
            row[0]
            for row in db.execute("""
            SELECT DISTINCT leads.id FROM leads JOIN runs ON runs.id=leads.run_id
              WHERE runs.mode IN ('live','showcase','recheck') AND runs.status IN ('complete','complete_with_errors','interrupted')
              AND EXISTS (SELECT 1 FROM events WHERE events.run_id=runs.id AND json_extract(events.data,'$.lead_id')=leads.id
                          AND json_extract(events.data,'$.stage')='Queue') AND json_extract(data,'$.status')
              IN ('ready','manual','skip','hold','blocked','dismissed')
              UNION
              SELECT usage.lead_id FROM usage LEFT JOIN runs ON runs.id=usage.run_id
              WHERE runs.id IS NULL AND usage.service='Exa'""")
        }


def dismissed_ids():
    with connect() as db:
        return {
            row[0]
            for row in db.execute(
                "SELECT lead_id FROM contact_actions WHERE state='dismissed'"
            )
        }


def contact_action(lead_id):
    with connect() as db:
        row = db.execute("SELECT * FROM contact_actions WHERE lead_id=?", (lead_id,)).fetchone()
        return dict(row) if row else {}


def set_contact_action(run_id, lead_id, state):
    """Record an operator decision for the license and retain the run it came from."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if state == "restore":
            db.execute("DELETE FROM contact_actions WHERE lead_id=? AND run_id=?", (lead_id, run_id))
            return
        if state not in {"contacted", "dismissed"}:
            raise ValueError("Choose contacted or dismissed.")
        row = db.execute("""
            SELECT r.mode, l.data->>'$.status' AS route,l.data->>'$.pitch.body' AS draft,
                   l.data->>'$.preview.url' AS url, l.data->>'$.preview.published' AS published,
                   coalesce(l.data->>'$.preview.content_verified',1) AS verified
            FROM leads l JOIN runs r ON r.id=l.run_id WHERE l.run_id=? AND l.id=?""", (run_id, lead_id)).fetchone()
        if row and row["mode"] == "demo" and state == "dismissed":
            db.execute("""UPDATE leads SET data=json_set(data,'$.original_status',coalesce(data->>'$.original_status',
                          data->>'$.status'),'$.status','dismissed') WHERE run_id=? AND id=?""", (run_id, lead_id))
            return
        if not row or row["mode"] not in {"live", "showcase", "recheck"} or row["route"] not in {"ready", "manual", "hold"}:
            raise ValueError("Choose a live business with a reviewed decision.")
        if state == "contacted" and (row["route"] == "hold" or not all(row[k] for k in ("draft", "url", "published", "verified"))):
            raise ValueError("Only a released draft can be marked contacted.")
        saved = db.execute("INSERT OR IGNORE INTO contact_actions VALUES(?,?,?,?)", (lead_id, run_id, state, now()))
        if not saved.rowcount:
            raise ValueError("This business already has a contact decision. Restore it before choosing again.")


def add_event(event):
    with connect() as db:
        db.execute(
            "INSERT INTO events(run_id,data) VALUES (?,?)",
            (event["run_id"], json.dumps(event, ensure_ascii=False)),
        )


def events(run_id):
    with connect() as db:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT data FROM events WHERE run_id=? ORDER BY seq", (run_id,))
        ]


def edit_lead(run_id, lead_id, change):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM runs WHERE id=? AND mode='sample'", (run_id,)).fetchone():
            raise ValueError("The bundled sample is a read-only recording.")
        if db.execute("SELECT 1 FROM contact_actions WHERE run_id=? AND lead_id=?", (run_id, lead_id)).fetchone():
            raise ValueError("Restore this lead before editing its archived draft.")
        row = db.execute("SELECT data FROM leads WHERE run_id=? AND id=?", (run_id, lead_id)).fetchone()
        if row is None:
            raise ValueError("This business is no longer in the run.")
        lead = json.loads(row[0])
        change(lead)
        db.execute(
            "UPDATE leads SET data=? WHERE run_id=? AND id=?",
            (json.dumps(lead, ensure_ascii=False), run_id, lead_id),
        )


def reserve(run_id, lead_id, service, maximum, service_limit, call_cap):
    """Reserve before dispatch so retries/concurrent leads cannot overspend."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        total, subtotal = db.execute(
            "SELECT coalesce(sum(dollars),0),coalesce(sum(CASE WHEN service=? THEN dollars END),0) FROM usage",
            (service,),
        ).fetchone()
        count = db.execute(
            "SELECT count(*) FROM usage WHERE run_id=? AND lead_id=? AND service=?",
            (run_id, lead_id, service),
        ).fetchone()[0]
        if count >= call_cap or subtotal + maximum > service_limit or total + maximum > 25:
            raise RuntimeError(f"{service} call or spending limit reached.")
        cur = db.execute(
            "INSERT INTO usage VALUES(NULL,?,?,?,?,?,?,?,?)",
            (run_id, lead_id, service, maximum, "reserved", 0, 0, now()),
        )
        return cur.lastrowid


def record_usage(request_id, dollars, input_tokens=0, output_tokens=0):
    with connect() as db:
        db.execute(
            "UPDATE usage SET dollars=?,state='reported',input_tokens=?,output_tokens=? WHERE id=?",
            (dollars, input_tokens, output_tokens, request_id),
        )


def usage(run_id=None):
    with connect() as db:
        sql = """SELECT service,state,count(*) AS calls,round(sum(dollars),6) AS dollars,
                 sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens FROM usage"""
        args = ()
        if run_id:
            sql += " WHERE run_id=?"
            args = (run_id,)
        return [dict(row) for row in db.execute(sql + " GROUP BY service,state", args)]


def setting(key, default=""):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key, value):
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))
