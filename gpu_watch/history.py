"""Explicit, evidence-backed historical interval correction helpers."""
import json
import math


def exclude_capacity_window(conn, host, start, end):
    if not (math.isfinite(start) and math.isfinite(end) and start < end):
        raise ValueError("invalid exclusion window")
    rows = conn.execute("select * from gpu_capacity_interval where host=? and end_ts>? and start_ts<?", (host,start,end)).fetchall()
    removed = 0.0
    for row in rows:
        item=dict(row)
        overlap=max(0.0,min(end,item["end_ts"])-max(start,item["start_ts"]))
        removed+=overlap
        conn.execute("delete from gpu_capacity_interval where id=?",(item["id"],))
        for a,b in [(item["start_ts"],min(start,item["end_ts"])),(max(end,item["start_ts"]),item["end_ts"])]:
            if b <= a: continue
            conn.execute("insert into gpu_capacity_interval(host,gpu_index,start_ts,end_ts,busy,memory_used_mib,memory_total_mib,seconds) values (?,?,?,?,?,?,?,?)",
                (host,item["gpu_index"],a,b,item["busy"],item["memory_used_mib"],item["memory_total_mib"],b-a))
    return removed


def apply_verified_idle_outage(conn, evidence):
    """Caller holds a write transaction and has made an online SQLite backup.

    This intentionally rejects busy intervals: attribution repair needs separate
    evidence. A marker makes source/local replay safe and idempotent.
    """
    key=evidence["key"]
    if conn.execute("select 1 from maintenance_meta where key=?",(key,)).fetchone():
        return False
    host,start,end=evidence["host"],evidence["start"],evidence["end"]
    if conn.execute("select 1 from gpu_usage_interval where host=? and end_ts>? and start_ts<? limit 1",(host,start,end)).fetchone():
        raise ValueError("outage has usage; separate attribution evidence required")
    for index,seconds in evidence["observed_seconds_removed"].items():
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid lifetime correction")
        updated=conn.execute("update gpu_runtime set observed_seconds=observed_seconds-? where host=? and gpu_index=? and observed_seconds>=?",(seconds,host,int(index),seconds))
        if updated.rowcount != 1: raise ValueError("lifetime correction is not applicable")
    exclude_capacity_window(conn,host,start,end)
    for row in evidence["daily_rows"]:
        conn.execute("update lab_daily_index set average_used_gb=?,observed_seconds=?,complete=?,updated_ts=? where lab_id=? and day=?",
            tuple(row[k] for k in ["average_used_gb","observed_seconds","complete","updated_ts","lab_id","day"]))
    for ts,event in [(start,"host_down"),(end,"host_recovered")]:
        conn.execute("insert into events(ts,host,gpu_index,event,note) values (?,?,null,?,?)",
            (ts,host,event,"Historical observation boundary; confirmed unusable period"))
    conn.execute("insert into maintenance_meta(key,value) values (?,?)",(key,json.dumps(evidence,separators=(",",":"))))
    return True
