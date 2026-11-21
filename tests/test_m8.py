from decimal import Decimal
import pytest
from app.errors import DomainError
from app.pipeline import calculate, execute, load_sample
from app import store
from app.modules.m8_milestones.rules import evaluate, RULING

PARAMS = {"paid_milestones": {"001": ["N1", "N2", "N3"], "002": ["N1", "N2", "N3"]},
          "contract_qty": {"001": "500", "002": "500"}}


@pytest.fixture
def milestone_result():
    return calculate(load_sample("milestone.csv"), PARAMS)


def row(item="001", period="2026-11", qty="400", price="120", claim="C1"):
    return {"claim_id": claim, "item": item, "period": period, "cumulative_qty": qty,
            "unit_price": price, "_line": 4, "_sources": [4]}


def test_ruling_uses_signed_cumulative_qty():
    "裁决：以本期已签认累计量判定跨越，400/500=0.8 触发N4，未达N5"
    r = evaluate([row()], {"parameters": {}, "paid_milestones": {"001": ["N1", "N2", "N3"]}})
    codes = [(p["item"], p["milestone_code"]) for p in r["rows"]]
    assert codes == [("001", "N4")]
    assert r["crossings"][-1]["completion_ratio"] == "0.8"


def test_historical_nodes_paid_once_not_reissued():
    "审计要求：历史已签节点只付一次，补量不倒改已签证"
    r = evaluate([row()], {"parameters": {}, "paid_milestones": {"001": ["N1", "N2", "N3"]}})
    assert all(c["status"] != "本期新增" or c["milestone_code"] == "N4" for c in r["crossings"])
    assert {c["milestone_code"] for c in r["crossings"] if c["status"] == "历史已付"} == {"N1", "N2", "N3"}
    assert r["metrics"]["historical_paid"] == 3 and r["metrics"]["new_milestones"] == 1


def test_two_nodes_crossed_in_one_period(milestone_result):
    "现场量跨过两个节点场景：本样例两清单项各新增N4，历史N1-N3不重付"
    m8 = milestone_result["stages"][7]
    assert m8["metrics"]["new_milestone_keys"] == ["001/N4", "002/N4"]
    assert m8["metrics"]["historical_paid"] == 6
    by_item = {p["item"]: p for p in m8["rows"]}
    assert by_item["001"]["net_payable"] == "10200.00"   # 12000 - 600 - 1200
    assert by_item["002"]["net_payable"] == "8500.00"    # 10000 - 500 - 1000
    assert m8["metrics"]["gross_total"] == "22000.00"
    assert m8["metrics"]["net_total"] == "18700.00"


def test_deduction_conservation(milestone_result):
    for p in milestone_result["stages"][7]["rows"]:
        assert Decimal(p["gross_amount"]) == Decimal(p["retention"]) + Decimal(p["advance_recovery"]) + Decimal(p["net_payable"])


def test_supplement_below_signed_qty_does_not_reverse(milestone_result):
    "同周期补量重复申报在M4隔离，节点仍以采纳的400已签量为准，来源可追溯"
    m4q = {q["reason"] for q in milestone_result["stages"][3]["quarantine"]}
    assert "DUPLICATE_PERIOD" in m4q
    paid = milestone_result["stages"][7]["rows"][0]
    assert paid["signed_qty"] == "400" and paid["_sources"] == [4]


def test_unapproved_change_quarantined_not_paid(milestone_result):
    reasons = {q["reason"] for q in milestone_result["stages"][7]["cumulative_quarantine"]}
    assert "UNAPPROVED_CHANGE" in reasons


def test_no_new_node_when_only_historical_crossed():
    "重跑无新增：不产生付款行，但保留跨越台账"
    r = evaluate([row(qty="300")], {"parameters": {}, "paid_milestones": {"001": ["N1", "N2", "N3"]}})
    assert r["rows"] == []
    assert r["metrics"]["new_milestones"] == 0 and r["metrics"]["crossed_milestones"] == 3
    assert r["metrics"]["net_total"] == "0.00"


def test_exact_threshold_only_n1():
    "恰好达到阈值即触发（ratio>=threshold）：100/500=0.2 仅触发N1"
    r = evaluate([row(qty="100")], {"parameters": {}, "paid_milestones": {}})
    assert [p["milestone_code"] for p in r["rows"]] == ["N1"]


def test_rerun_same_input_is_idempotent():
    "同一节点重跑不得重复付款：同run_id、台账仅一条"
    a = execute(load_sample("milestone.csv"), PARAMS)
    b = execute(load_sample("milestone.csv"), PARAMS)
    assert a["run_id"] == b["run_id"] and len(store.history()) == 1
    assert store.paid_milestones() == {"001": ["N4"], "002": ["N4"]}


def test_ledger_conflict_rolls_back_and_keeps_old_visa():
    "切换失败：台账已被他批次占用时整体回滚，旧批次与旧签证保留"
    res = calculate(load_sample("milestone.csv"), PARAMS)
    run_id = res["run_id"]
    with store.connect() as db:
        db.execute("INSERT INTO milestone_payments VALUES (?,?,?,?,?,datetime('now'))",
                   ("001", "N4", "concurrent-run", "本期新增", "10200.00"))
    with pytest.raises(DomainError) as e:
        store.save_run_with_milestones(run_id, res)
    assert e.value.code == "MILESTONE_ALREADY_PAID" and e.value.status == 409
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT run_id FROM milestone_payments").fetchone()[0] == "concurrent-run"


def test_second_run_only_pays_new_higher_node():
    "台账驱动：第二批量到500时，只签发N5，N1-N4不重付"
    execute(load_sample("milestone.csv"), PARAMS)  # 首批签发N4
    paid = store.paid_milestones()
    for item in ("001", "002"):
        paid[item] = sorted(set(paid.get(item, [])) | {"N1", "N2", "N3"})
    r = evaluate([row(qty="500"), row(item="002", qty="500", price="100", claim="C2")],
                 {"parameters": {"contract_qty": {"001": "500", "002": "500"}},
                  "paid_milestones": paid})
    assert sorted(p["milestone_code"] for p in r["rows"]) == ["N5", "N5"]
    assert all(p["status"] == "本期新增" for p in r["rows"])


def test_unknown_paid_milestone_quarantined():
    r = evaluate([row()], {"parameters": {}, "paid_milestones": {"001": ["N9"]}})
    assert any(q["reason"] == "UNKNOWN_PAID_MILESTONE" for q in r["quarantine"])


def test_paid_node_without_any_claim_quarantined():
    r = evaluate([row()], {"parameters": {}, "paid_milestones": {"999": ["N1"]}})
    assert any(q["reason"] == "PAID_MILESTONE_WITHOUT_CLAIM" for q in r["quarantine"])


def test_missing_contract_qty_quarantined():
    r = evaluate([row(item="007")], {"parameters": {"contract_qty": {}}, "paid_milestones": {}})
    assert r["quarantine"][0]["reason"] == "NO_CONTRACT_QTY"


def test_invalid_threshold_rejected():
    bad = [{"code": "X", "threshold": "1.5", "pay_ratio": "0.2"}]
    with pytest.raises(DomainError):
        evaluate([row()], {"parameters": {"milestones": bad}, "paid_milestones": {}})


def test_non_increasing_threshold_rejected():
    bad = [{"code": "A", "threshold": "0.5"}, {"code": "B", "threshold": "0.4"}]
    with pytest.raises(DomainError):
        evaluate([row()], {"parameters": {"milestones": bad}, "paid_milestones": {}})


def test_payment_business_key_unique():
    ids = [p["payment_id"] for p in evaluate(
        [row(), row(item="002", price="100", claim="C2")],
        {"parameters": {}, "paid_milestones": {}})["rows"]]
    assert len(ids) == len(set(ids))


def test_ruling_text_present_in_metrics(milestone_result):
    assert milestone_result["stages"][7]["metrics"]["ruling"] == RULING
    assert "已签认累计工程量" in RULING


def test_milestone_http_endpoint_and_page(client):
    assert client.get("/api/milestones").status_code == 200
    assert client.get("/milestones").status_code == 200
    r = client.post("/api/runs", json={"sample": "milestone.csv"})
    assert r.status_code == 200 and len(r.get_json()["stages"]) == 8


def test_http_rerun_returns_stored_visa(client):
    "同源重跑返回原签证，节点与净额不变，不产生新批次"
    first = client.post("/api/runs", json={"sample": "milestone.csv"}).get_json()
    second = client.post("/api/runs", json={"sample": "milestone.csv"}).get_json()
    assert first["run_id"] == second["run_id"]
    assert second["stages"][7]["metrics"]["new_milestone_keys"] == ["001/N4", "002/N4"]
    assert second["stages"][7]["metrics"]["net_total"] == "18700.00"
    assert len(client.get("/api/runs").get_json()["runs"]) == 1


def test_archive_contains_milestone_evidence(client):
    import csv, io
    p = client.post("/api/runs", json={"sample": "milestone.csv"}).get_json()
    data = client.get("/api/runs/" + p["run_id"] + "/archive").data.decode("utf-8-sig")
    kinds = {r[3] for r in csv.reader(io.StringIO(data))}
    assert "accepted" in kinds and "quarantine" in kinds
