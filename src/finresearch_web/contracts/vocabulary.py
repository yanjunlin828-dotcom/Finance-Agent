"""Display labels for source-defined semantics; no research policy overrides."""

B1_STATIONS = [
    ("validate_context", "确认范围", "建立事实"),
    ("load_financials", "核对财务", "建立事实"),
    ("calculate", "计算指标", "建立事实"),
    ("detect_phenomena", "识别现象", "形成解释"),
    ("propose_hypotheses", "提出候选解释", "形成解释"),
    ("collect_fixed_evidence", "查找年报说明", "形成解释"),
    ("review_claims", "整理结论与未决问题", "审查与成文"),
    ("write_report", "整理报告", "审查与成文"),
    ("validate_report", "核对报告记录", "审查与成文"),
]
B2_STATIONS = [("audit_gaps", "识别缺口"), ("plan_actions", "选择动作"), ("execute_actions", "执行与核验"), ("write_supplement_report", "整理补查报告")]
TASK_LABELS = {
    "CREATED": "已创建，准备执行", "RUNNING": "正在研究", "COMPLETED": "研究已完成", "PARTIAL": "报告仍有未决问题",
    "WAITING_INPUT": "等待你的决定", "CANCELLED": "研究已取消", "FAILED": "研究执行失败", "BLOCKED": "需要核对后再处理",
}
STOP_LABELS = {
    "ROUND_LIMIT": "达到补查轮数上限", "ACTION_BUDGET": "达到工具动作上限", "NO_PROGRESS": "连续补查没有新增进展",
    "NO_ACTIONABLE_GAPS": "当前没有合法可执行动作，仍有局限", "ALL_RESOLVED": "当前记录判定缺口已关闭",
    "MODEL_PLAN_REJECTED_OR_BUDGET": "动作计划、模型调用或预算受到限制", "TOOL_FAILURE": "工具执行失败，保留已有成果",
    "QUOTE_REVIEW_FAILED": "新增引用没有通过复核", "NEEDS_INPUT": "需要明确决定或资料", "REQUIRES_NEW_SNAPSHOT": "需要新资料快照",
}
TOOL_RESULT_LABELS = {"FOUND": "找到候选资料，接受状态另行核验", "NO_EVIDENCE": "本轮未召回适用资料，不代表未披露", "REVIEW_REQUIRED": "资料仍需复核", "NEEDS_INPUT": "需要你的决定", "REQUIRES_NEW_SNAPSHOT": "需要新资料快照"}
FOLLOWUP_LABELS = {"ANSWERED_FROM_REVIEWED_RECORDS": "基于已审核记录回答", "NEEDS_CLARIFICATION": "请明确追问专题", "INSUFFICIENT_EVIDENCE": "当前记录不足以回答", "NEW_RUN_REQUIRED": "范围已变化，需要新研究"}
OBSERVATION_LABELS = {"OBSERVED": "有来源数值", "MISSING": "缺少数值", "NOT_APPLICABLE": "不适用", "REVIEW_REQUIRED": "需要复核"}
CALCULATION_LABELS = {"VALID": "有效计算", "MISSING_INPUT": "缺少输入", "ZERO_DENOMINATOR": "分母为零", "NEGATIVE_BASE": "负基数，不输出普通增长率", "INCOMPARABLE": "口径不可比较"}
OPERATION_LABELS = {"VIEW_REPORT": "查看报告", "VIEW_STEPS": "查看过程", "VIEW_EVIDENCE": "查看原文", "FOLLOWUP": "追问已审核记录", "CANCEL": "取消研究", "CLARIFY": "提交决定", "RESUME": "恢复研究", "NEW_TASK": "新建研究"}
GAP_LABELS = {"OPEN": "待处理", "RESOLVED": "已满足该缺口关闭条件", "LIMITED": "保留局限", "WAITING_INPUT": "等待决定", "FAILED": "本次处理失败"}
