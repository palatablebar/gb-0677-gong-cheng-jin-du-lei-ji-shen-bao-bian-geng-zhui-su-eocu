from decimal import Decimal
from app.config import DEFAULT_MILESTONES, DEFAULT_CONTRACT_QTY
from app.values import number, money
from app.reasons import reject, sources
from app.errors import DomainError

# 裁决：节点跨越一律以“本期已签认工程量”（经前序审核通过、带来源行的累计量）为准。
# 历史已签证节点由付款台账（context.paid_milestones）登记，只付一次；
# 补量只能触发高于最高已付节点的新节点，不倒改已签证。
RULING = ("节点跨越以本期已签认累计工程量为准：仅对超过最高已付节点的新跨节点签发，"
          "历史已签证节点只付一次，后续补量不得倒改已签证。")


def _milestones(params):
    nodes = params.get("milestones", DEFAULT_MILESTONES)
    if not isinstance(nodes, list) or not nodes:
        raise DomainError("BAD_MILESTONES", "节点规则需要非空列表")
    result = []
    last_threshold = Decimal(-1)
    for node in nodes:
        if not isinstance(node, dict) or not str(node.get("code", "")).strip():
            raise DomainError("BAD_MILESTONES", "节点缺少编码")
        threshold = number(node["threshold"])
        ratio = number(node.get("pay_ratio", "0"))
        if not Decimal(0) <= threshold <= 1 or not Decimal(0) <= ratio <= 1:
            raise DomainError("BAD_MILESTONES", "节点阈值或付款比例超出[0,1]")
        if threshold <= last_threshold:
            raise DomainError("BAD_MILESTONES", "节点阈值必须严格递增")
        last_threshold = threshold
        result.append({"code": str(node["code"]), "threshold": threshold, "pay_ratio": ratio})
    return result


def _paid_index(paid):
    index = {}
    for item, codes in (paid or {}).items():
        if not isinstance(codes, list):
            raise DomainError("BAD_PAID_MILESTONES", "已付节点需要按清单项给出编码列表")
        index[str(item)] = {str(code) for code in codes}
    return index


def _ratio_text(value):
    text = str(value.quantize(Decimal("0.0000000001"))).rstrip("0").rstrip(".")
    return text or "0"


def evaluate(rows, context):
    context = context or {}
    params = context.get("parameters", {}) or {}
    retention = number(params.get("retention_rate", "0.05"))
    advance = number(params.get("advance_rate", "0.10"))
    if not Decimal(0) <= retention <= 1 or not Decimal(0) <= advance <= 1 or retention + advance > 1:
        raise DomainError("BAD_DEDUCTION", "扣回比例无效")
    nodes = _milestones(params)
    contract_qty = params.get("contract_qty", DEFAULT_CONTRACT_QTY)
    if not isinstance(contract_qty, dict):
        raise DomainError("BAD_CONTRACT_QTY", "合同总量需要按清单项配置")
    paid_index = _paid_index(context.get("paid_milestones"))

    # 每个清单项只取最新一期已签认记录作为本期累计（补量/重复申报在 M1/M4 已被隔离）。
    latest = {}
    for row in rows:
        item = str(row.get("item", ""))
        period = str(row.get("period", ""))
        current = latest.get(item)
        if current is None or (period, str(row.get("claim_id", ""))) > (current["period"], str(current.get("claim_id", ""))):
            latest[item] = row

    payments = []
    crossings = []
    quarantine = list(context.get("quarantine_milestones", []))
    unknown_paid = set()
    for item, paid_codes in paid_index.items():
        if item not in latest:
            unknown_paid.update((item, code) for code in paid_codes)
    for item, code in sorted(unknown_paid):
        quarantine.append({"_line": 0, "_sources": [], "reason": "PAID_MILESTONE_WITHOUT_CLAIM",
                           "detail": f"清单项{item}节点{code}登记为已付，但本期无已签认申报可核对"})

    for item in sorted(latest):
        row = latest[item]
        if item not in contract_qty:
            quarantine.append(reject(row, "NO_CONTRACT_QTY", f"清单项{item}未登记合同总量，无法计算完成比例"))
            continue
        total_qty = number(contract_qty[item])
        if total_qty <= 0:
            raise DomainError("BAD_CONTRACT_QTY", f"清单项{item}合同总量必须为正")
        signed_qty = number(row["cumulative_qty"])
        if signed_qty < 0:
            quarantine.append(reject(row, "NEGATIVE_CUMULATIVE", "已签认累计量不能为负"))
            continue
        unit_price = number(row.get("unit_price", "0"))
        ratio = (signed_qty / total_qty)
        paid_codes = paid_index.get(item, set())
        valid_codes = {node["code"] for node in nodes}
        for code in sorted(paid_codes - valid_codes):
            quarantine.append(reject(row, "UNKNOWN_PAID_MILESTONE", f"已付节点{code}不在节点规则内"))
        for node in nodes:
            crossed = ratio >= node["threshold"]
            already_paid = node["code"] in paid_codes
            if not crossed:
                continue
            gross = money(total_qty * unit_price * node["pay_ratio"])
            crossing = {
                "item": item, "milestone_code": node["code"],
                "threshold": str(node["threshold"]), "completion_ratio": _ratio_text(ratio),
                "signed_qty": str(signed_qty), "contract_qty": str(total_qty),
                "signed_period": row.get("period", ""), "claim_id": str(row.get("claim_id", "")),
                "status": "历史已付" if already_paid else "本期新增",
                "gross_amount": gross,
            }
            crossings.append(crossing)
            if already_paid:
                continue
            gross_dec = number(gross)
            ret = number(money(gross_dec * retention))
            adv = number(money(gross_dec * advance))
            net = money(gross_dec - ret - adv)
            payments.append({
                "payment_id": f"{item}-{node['code']}",
                "claim_id": str(row.get("claim_id", "")),
                "item": item,
                "milestone_code": node["code"],
                "signed_period": row.get("period", ""),
                "signed_qty": str(signed_qty),
                "contract_qty": str(total_qty),
                "completion_ratio": _ratio_text(ratio),
                "threshold": str(node["threshold"]),
                "unit_price": str(unit_price),
                "gross_amount": gross,
                "retention": money(ret),
                "advance_recovery": money(adv),
                "net_payable": net,
                "ruling_basis": RULING,
                "status": "本期新增",
                "_sources": list(sources(row)),
            })

    ids = [p["payment_id"] for p in payments]
    if len(ids) != len(set(ids)):
        raise DomainError("MILESTONE_DUPLICATE", "节点付款业务键重复")

    gross_total = sum((number(p["gross_amount"]) for p in payments), Decimal(0))
    ret_total = sum((number(p["retention"]) for p in payments), Decimal(0))
    adv_total = sum((number(p["advance_recovery"]) for p in payments), Decimal(0))
    net_total = sum((number(p["net_payable"]) for p in payments), Decimal(0))
    new_codes = {(p["item"], p["milestone_code"]) for p in payments}
    metrics = {
        "items": len(latest),
        "crossed_milestones": len(crossings),
        "historical_paid": sum(c["status"] == "历史已付" for c in crossings),
        "new_milestones": len(payments),
        "gross_total": money(gross_total),
        "retention_total": money(ret_total),
        "advance_total": money(adv_total),
        "net_total": money(net_total),
        "quarantine": len(quarantine),
        "ruling": RULING,
        "new_milestone_keys": sorted(f"{item}/{code}" for item, code in new_codes),
    }
    return {"rows": payments, "quarantine": quarantine, "metrics": metrics, "crossings": crossings}
