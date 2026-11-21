import sqlite3, json
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "records.db", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    # 节点付款台账：同一清单项同一节点全局唯一，重跑/并发都无法重复付款
    db.execute(
        "CREATE TABLE IF NOT EXISTS milestone_payments "
        "(item TEXT NOT NULL,milestone_code TEXT NOT NULL,run_id TEXT NOT NULL,status TEXT NOT NULL,"
        "net_payable TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "PRIMARY KEY(item,milestone_code))"
    )
    return db


def save(run_id, payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)", (run_id, json.dumps(payload, ensure_ascii=False)))
    return get(run_id)


def get(run_id):
    with connect() as db:
        row = db.execute("SELECT payload,version,decision FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "批次不存在", 404)
    return {**json.loads(row[0]), "version": row[1], "decision": row[2]}


def find(run_id):
    with connect() as db:
        row = db.execute("SELECT payload,version,decision FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    return {**json.loads(row[0]), "version": row[1], "decision": row[2]}


def latest():
    with connect() as db:
        row = db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None


def history():
    with connect() as db:
        return [{"run_id": i, "version": v, "decision": d} for i, v, d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]


def review(run_id, version, decision):
    if decision not in {"已复核", "退回复核"}:
        raise DomainError("BAD_DECISION", "复核状态无效")
    with connect() as db:
        cursor = db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?", (decision, run_id, version))
        if cursor.rowcount != 1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise DomainError("NOT_FOUND", "批次不存在", 404)
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
    return get(run_id)


def paid_milestones():
    """从付款台账读取各清单项已签证节点；节点付款裁决必须以已持久化的签证为准。"""
    with connect() as db:
        rows = db.execute("SELECT item,milestone_code FROM milestone_payments ORDER BY item,milestone_code").fetchall()
    index = {}
    for item, code in rows:
        index.setdefault(item, []).append(code)
    return index


def save_run_with_milestones(run_id, payload):
    """原子写入：批次与节点台账同一事务。任一步失败整体回滚，旧批次与旧签证原样保留。"""
    payments = payload["stages"][-1].get("rows", [])
    with connect() as db:
        try:
            db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)", (run_id, json.dumps(payload, ensure_ascii=False)))
            for payment in payments:
                db.execute(
                    "INSERT INTO milestone_payments(item,milestone_code,run_id,status,net_payable) VALUES (?,?,?,?,?)",
                    (payment["item"], payment["milestone_code"], run_id, payment["status"], payment["net_payable"]),
                )
        except sqlite3.IntegrityError:
            db.rollback()
            if payments:
                placeholders = ",".join("?" * len(payments))
                conflict = db.execute(
                    f"SELECT item,milestone_code,run_id FROM milestone_payments WHERE item IN ({placeholders})",
                    [p["item"] for p in payments],
                ).fetchall()
                keys = {(p["item"], p["milestone_code"]) for p in payments}
                dup = [f"{item}/{code}(批次{rid[:8]})" for item, code, rid in conflict if (item, code) in keys]
            else:
                dup = []
            raise DomainError("MILESTONE_ALREADY_PAID",
                              "节点已签证付款，禁止重复支付：" + "、".join(dup), 409)
    return get(run_id)
