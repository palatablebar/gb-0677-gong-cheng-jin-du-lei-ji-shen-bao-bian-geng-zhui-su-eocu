# 计量定义

claim_id唯一；item为清单编码，支持钢筋与001等别名。period为YYYY-MM。累计数量按t或m³保存，当前周期工程量=本次累计-前期已审累计。previous_revisions参数可指定更正后的前审数量，重新计算形成不同批次，不覆盖旧证据。

C0原始价100元，C1自2026-11生效为120元，DRAFT未批准不可结算。负进度必须reversal=approved作为已批冲回标记。扣留质保金5%、预付款扣回10%分别按分量化，净额=本期总额-质保金-预付款扣回，负进度同步冲回扣留。reported为原申报净额，差异=复算净额-reported。

业务ID和超大整数按文本保形；Decimal参与数量金额计算，对外序列化为字符串。哨兵集合定义在app/values.py，缺失值不能当零。所有时间带UTC偏移，先转UTC比较，业务日按指定地区划分。区间统一[start,end)。重复业务键保留第一条、完全相同行计重复；值冲突行隔离且保留原值。空文件、全空白、仅表头分开拒绝。

运行清单包括输入sha256、规则版本、参数、生成时刻与耗时。源行编号以CSV解析器的物理结束行号表示，引号内换行不增加记录数。输入超过4MB返回413，非法表头和类型返回422，失效版本复核返回409。

## 节点付款（M8）

按清单项取最新一期已签认记录，`completion_ratio=已签累计量/合同总量`，达到节点`threshold`即跨越；默认节点N1-N5阈值0.2/0.4/0.6/0.8/1.0，每节点按合同额（合同总量×当期已签单价）的`pay_ratio`计价。仅对超过最高已付节点的新节点产出付款行，字段含`payment_id`（item-节点编码，业务键唯一）、`signed_period`、`signed_qty`、`completion_ratio`、`threshold`、`gross_amount`、`retention`、`advance_recovery`、`net_payable`（扣回净额）、`ruling_basis`与来源行`_sources`。历史已付节点进入`crossings`但不重复计价。隔离码：`NO_CONTRACT_QTY`、`UNKNOWN_PAID_MILESTONE`、`PAID_MILESTONE_WITHOUT_CLAIM`、`NEGATIVE_CUMULATIVE`。台账表`milestone_payments`主键`(item,milestone_code)`，重跑或并发占用返回409。