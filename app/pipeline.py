import time
from app.config import ROOT, MODULES, SAMPLES
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest, manifest
from app import store
from app.modules.m1_claims.rules import evaluate as stage1
from app.modules.m2_billitems.rules import evaluate as stage2
from app.modules.m3_changes.rules import evaluate as stage3
from app.modules.m4_increment.rules import evaluate as stage4
from app.modules.m5_deductions.rules import evaluate as stage5
from app.modules.m6_valuation.rules import evaluate as stage6
from app.modules.m7_certificates.rules import evaluate as stage7
from app.modules.m8_milestones.rules import evaluate as stage8
STAGES = [stage1, stage2, stage3, stage4, stage5, stage6, stage7, stage8]


def load_sample(name):
    if name not in SAMPLES:
        raise DomainError("BAD_SAMPLE", "样例不存在", 404)
    return (ROOT / "sample_data" / name).read_bytes()


def calculate(raw, params=None, stop=7):
    params = params or {}
    started = time.monotonic()
    # 付款台账在计算前快照：重跑同一输入时已付节点稳定，保证同一节点不会被二次签发。
    # 参数声明的历史已签证节点与持久台账合并（台账优先），用于无台账环境演示“只付一次”。
    ledger = store.paid_milestones()
    declared = params.get("paid_milestones", {}) or {}
    paid = {item: set(codes) for item, codes in ledger.items()}
    for item, codes in declared.items():
        paid.setdefault(str(item), set()).update(str(code) for code in codes)
    context = {"parameters": params, "raw": raw, "quarantine": [],
               "paid_milestones": {item: sorted(codes) for item, codes in paid.items()}}
    stages = []
    rows = []
    for index, stage in enumerate(STAGES[:stop + 1]):
        result = stage(raw if index == 0 else rows, context)
        result = scalar(result)
        rows = result["rows"]
        context["quarantine"].extend(result.get("quarantine", []))
        if index == 0:
            context["intake"] = result["metrics"]
        result["cumulative_quarantine"] = list(context["quarantine"])
        context[MODULES[index][0]] = result
        stages.append(result)
    return {"run_id": digest(raw, params)[:20], "stages": stages, "manifest": manifest(raw, params, started), "parameters": params}


def execute(raw, params=None):
    result = calculate(raw, params)
    # 同源重跑复用已存批次：run_id由输入摘要+参数+规则版本派生，
    # 命中即返回原签证（含原节点付款行），同一节点绝不二次签发。
    existing = store.find(result["run_id"])
    if existing is not None:
        return existing
    # 含节点付款（M8）的批次走台账原子写入；台账冲突时整体回滚并保留旧签证。
    if len(result["stages"]) == len(STAGES):
        return store.save_run_with_milestones(result["run_id"], result)
    return store.save(result["run_id"], result)


def stage_preview(index, payload):
    if "rows" in payload and index > 0:
        if not isinstance(payload["rows"], list) or any(not isinstance(row, dict) for row in payload["rows"]):
            raise DomainError("BAD_ROWS", "记录需要对象列表")
        if not isinstance(payload.get("context", {}), dict):
            raise DomainError("BAD_CONTEXT", "上下文需要对象")
        context = payload.get("context", {"parameters": {}, "quarantine": []})
        ledger = store.paid_milestones()
        declared = context.get("parameters", {}).get("paid_milestones", {}) or {}
        paid = {item: set(codes) for item, codes in ledger.items()}
        for item, codes in declared.items():
            paid.setdefault(str(item), set()).update(str(code) for code in codes)
        for item, codes in (context.get("paid_milestones", {}) or {}).items():
            paid.setdefault(str(item), set()).update(str(code) for code in codes)
        context["paid_milestones"] = {item: sorted(codes) for item, codes in paid.items()}
        return scalar(STAGES[index](payload["rows"], context))
    raw = payload.get("csv")
    raw = raw.encode() if isinstance(raw, str) else load_sample(payload.get("sample", "dirty.csv"))
    return calculate(raw, payload.get("parameters", {}), index)["stages"][-1]
