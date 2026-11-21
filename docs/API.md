# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

M8节点付款（`/api/milestones`）额外返回`crossings`（全部已跨节点及历史已付/本期新增裁决）与`metrics.ruling`（裁决依据）。参数支持`milestones`（节点code/threshold/pay_ratio）、`contract_qty`（按清单项合同总量）、`retention_rate`、`advance_rate`、`paid_milestones`（按清单项声明历史已签证节点编码，与持久台账合并）。`POST /api/runs`选择`milestone.csv`时自动带入历史已付N1-N3参数。含M8的批次经`milestone_payments`台账原子写入，节点重复付款返回409 `MILESTONE_ALREADY_PAID`且不产生部分写入。
