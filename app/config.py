from pathlib import Path
import os
ROOT=Path(__file__).resolve().parent.parent
DATA_DIR=Path(os.environ.get("DATA_DIR", "/tmp/progressclaim"))
TITLE='工程进度累计申报与变更追溯'
SHORT='progressclaim'
COLUMNS=['claim_id', 'item', 'period', 'cumulative_qty', 'previous_qty', 'change_id', 'reversal', 'reported', 'note']
MODULES=[('claims', '申报采信'), ('billitems', '清单映射'), ('changes', '变更定版'), ('increment', '累计还原'), ('deductions', '扣回核算'), ('valuation', '进度对账'), ('certificates', '签证归档'), ('milestones', '节点付款')]
SAMPLES=["dirty.csv","clean.csv","legacy.csv","milestone.csv"]
RULE_VERSION="2026.11.21-r2"
# 节点付款默认规则：累计完成比例达到阈值即触发，节点金额=合同总量×当期已签单价×节点比例
DEFAULT_MILESTONES=[{"code":"N1","threshold":"0.20","pay_ratio":"0.20"},{"code":"N2","threshold":"0.40","pay_ratio":"0.20"},{"code":"N3","threshold":"0.60","pay_ratio":"0.20"},{"code":"N4","threshold":"0.80","pay_ratio":"0.20"},{"code":"N5","threshold":"1.00","pay_ratio":"0.20"}]
DEFAULT_CONTRACT_QTY={"001":"500","002":"500"}
