"""Binary host collection state and safe event metadata."""
import json

def observation_event_details(error, failures, legacy=False):
    text = str(error or "").lower()
    cause = ("legacy_unclassified" if legacy else "timeout" if "timeout" in text or "timed out" in text
             else "authentication" if any(s in text for s in ["credential","permission denied","publickey"])
             else "connection" if any(s in text for s in ["connection","network","route to host"])
             else "probe_failure")
    return json.dumps({"cause":cause,"failures":failures},separators=(",",":"))

def event_details(note):
    try:
        value=json.loads(note or "{}")
        return value if isinstance(value,dict) else {}
    except (TypeError,ValueError):
        return {}


def migrate_host_availability(conn):
    """Retire cached per-device decisions once; preserve historical records."""
    key = "simple_host_availability_v1"
    if conn.execute("select 1 from maintenance_meta where key=?", (key,)).fetchone():
        return
    for row in conn.execute("select host,online,consecutive_failures,gpu_errors_json,usable_gpu_indices_json from host_runtime").fetchall():
        failed = row["consecutive_failures"] or json.loads(row["gpu_errors_json"] or "{}")
        # Preserve an already unavailable host until a new complete sample.
        failed = failed or row["usable_gpu_indices_json"] == "[]"
        conn.execute("update host_runtime set online=?,gpu_errors_json='{}',usable_gpu_indices_json=null,gpu_health_json='{}' where host=?",
                     (int(bool(row["online"]) and not failed),row["host"]))
    conn.execute("insert into maintenance_meta(key,value) values (?,?)", (key,"complete"))
