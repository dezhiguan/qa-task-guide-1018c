import json
import re
from typing import Any, Dict, List

DF_PATTERN = re.compile(r"(?<![A-Za-z0-9_])DF-(\d+)(?!\d)(?![_\-])")

EXEC_PATTERNS = [
    r"(?:直接|自动|马上|立刻|现在|请|帮我|帮忙|务必|必须).{0,6}(?:修改|合并|发布|部署|执行|上线).{0,6}(?:任务|上线|生产|代码)?",
    r"(?:执行|perform|deploy).{0,6}(?:生产|production|deployment|变更)",
    r"(?:bypass|ignore|忽略|跳过|跳过核验|绕过).{0,12}(?:规则|gates|核验|审批|all rules|安全|门禁)",
    r"(?:fabricate|编造|伪造).{0,12}(?:记录|PASS|successful|状态)",
    r"(?:改成|强行|也请改成|必须改成|改成).{0,4}PASS",
    r"即使.{0,12}(?:失败|空列表|BLOCKED|返回空).{0,12}(?:输出|改成|PASS|给|报)",
    r"(?:不要|不得|禁止|不能|不|别).{0,2}(?:调用|使用).{0,20}(?:直接|给出|告诉)",
    r"(?:建议|要求|需要|请|帮我).{0,6}(?:执行|发布|部署|上线|合并|修改)",
    r"(?:列出|给出|推荐).{0,12}(?:WRITE|HIGH|写|高).{0,12}(?:执行|发布|部署|上线)",
    r"(?:当成|视为|当作).{0,8}(?:管理员|老板|批准).{0,8}(?:执行|变更|发布|上线|合并)",
]

DENIAL_MARKERS = [
    "不代表", "不是", "禁止", "无需", "不需要", "不执行", "不要", "不", "别",
    "do not", "don't", "not ", "without", "不修改", "不更改", "不能", "不得",
]

NEGATED_TRIGGER = re.compile(r"(?:不代表|不是|禁止|无需|不需要|不执行|不要|别|do not|don't|not|without|不|不能|不得)\s*$")

STATUS_MAP = {
    "RUN": "运行中",
    "WAIT": "等待中",
    "HUMAN": "等待人工处理",
    "QUEUED": "排队中",
    "DONE": "已完成",
    "FAIL": "失败",
    "CANCEL": "已取消",
}

VALID_STATUSES = set(STATUS_MAP.keys())


def _extract_job_ids(text: str) -> List[str]:
    matches = DF_PATTERN.findall(text)
    return list(dict.fromkeys(matches))


def _is_negated_context(prefix: str, full_text: str, match_end: int) -> bool:
    tail = prefix.strip()[-25:]
    for marker in DENIAL_MARKERS:
        if tail.endswith(marker):
            return True
    # Also check if the trigger word itself is preceded by negation within 6 chars
    neg_zone = prefix.strip()[-8:]
    if NEGATED_TRIGGER.search(neg_zone):
        return True
    return False


def _has_execution_request(text: str) -> bool:
    for pat in EXEC_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            prefix = text[:m.start()]
            if _is_negated_context(prefix, text, m.end()):
                continue
            return True
    return False


def _validate_job_records(records: Any) -> tuple:
    if not isinstance(records, list) or len(records) == 0:
        return False, "任务记录为空或格式异常"
    for rec in records:
        if not isinstance(rec, dict):
            return False, "任务记录包含非字典元素"
        status = rec.get("status")
        if status is None:
            return False, "任务记录缺少status字段"
        if not isinstance(status, str) or not status.strip():
            return False, "任务记录status为空或非字符串"
        if status.strip().upper() not in VALID_STATUSES:
            return False, "任务记录status值无效：" + str(status)
    return True, ""


def _validate_catalog(catalog: Any) -> tuple:
    if not isinstance(catalog, list) or len(catalog) == 0:
        return False, "工具目录为空或格式异常"
    for item in catalog:
        if not isinstance(item, dict):
            return False, "工具目录包含非字典元素"
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return False, "工具目录条目缺少有效name"
        access = item.get("access")
        if not isinstance(access, str) or not access.strip():
            return False, "工具目录条目缺少有效access"
        risk = item.get("risk")
        if not isinstance(risk, str) or not risk.strip():
            return False, "工具目录条目缺少有效risk"
    return True, ""


def _build_answer(job_records: List[Dict], catalog: List[Dict]) -> str:
    parts = []
    if job_records:
        rec = job_records[0]
        status = str(rec.get("status", "")).strip().upper()
        cn_status = STATUS_MAP.get(status, status)
        parts.append("任务当前状态：" + cn_status)
    safe_tools = [
        t for t in catalog
        if str(t.get("access", "")).strip().upper() == "READ"
        and str(t.get("risk", "")).strip().upper() == "LOW"
    ]
    if safe_tools:
        names = [str(t.get("name", "")) for t in safe_tools if t.get("name")]
        parts.append("推荐只读低风险工具：" + "、".join(names))
    else:
        parts.append("当前目录中无READ+LOW工具可推荐")
    parts.append("以上为信息查询结果，不执行任何变更或发布操作。")
    return "\n".join(parts)


async def execute(data: dict, ctx) -> Dict[str, Any]:
    text = str(data.get("text") or "").strip()
    if not text:
        return {"decision": "BLOCKED", "answer": "", "block_reason": "输入为空", "evidence_sources": [], "steps": []}

    ids = _extract_job_ids(text)
    if len(ids) == 0:
        return {"decision": "BLOCKED", "answer": "", "block_reason": "未找到有效的DF-编号任务", "evidence_sources": [], "steps": []}
    if len(ids) > 1:
        return {"decision": "BLOCKED", "answer": "", "block_reason": "检测到多个不同任务编号，仅支持单任务查询", "evidence_sources": [], "steps": []}

    job_id = "DF-" + ids[0]

    if _has_execution_request(text):
        return {"decision": "BLOCKED", "answer": "", "block_reason": "输入包含实际执行或变更发布请求，本岗位仅提供信息", "evidence_sources": [], "steps": []}

    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=job_id)
    except Exception as e:
        return {"decision": "BLOCKED", "answer": "", "block_reason": "devflow.job.read调用失败：" + str(e), "evidence_sources": [], "steps": []}

    ok, msg = _validate_job_records(job_result)
    if not ok:
        return {"decision": "BLOCKED", "answer": "", "block_reason": msg, "evidence_sources": [], "steps": []}

    try:
        catalog_result = await ctx.tools.call("devflow.catalog.search", query="")
    except Exception as e:
        return {"decision": "BLOCKED", "answer": "", "block_reason": "devflow.catalog.search调用失败：" + str(e), "evidence_sources": [], "steps": []}

    ok, msg = _validate_catalog(catalog_result)
    if not ok:
        return {"decision": "BLOCKED", "answer": "", "block_reason": msg, "evidence_sources": [], "steps": []}

    evidence_sources = [
        {"tool": "devflow.job.read", "jobId": job_id, "count": len(job_result)},
        {"tool": "devflow.catalog.search", "query": "", "count": len(catalog_result)},
    ]
    steps = [
        {"step": 1, "action": "extract_job_id", "result": job_id},
        {"step": 2, "action": "call_devflow.job.read", "result": str(len(job_result)) + " records"},
        {"step": 3, "action": "call_devflow.catalog.search", "result": str(len(catalog_result)) + " tools"},
    ]

    try:
        prompt_obj = await ctx.prompt("role")
        system_msg = prompt_obj.compile()
        user_payload = json.dumps({"input": text, "evidence": {"job_records": job_result, "catalog": catalog_result}}, ensure_ascii=False)
        model_raw = await ctx.llm.chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": user_payload}],
            prompt=prompt_obj,
        )
    except Exception:
        return {"decision": "BLOCKED", "answer": "", "block_reason": "模型调用异常，无法完成判断", "evidence_sources": evidence_sources, "steps": steps}

    try:
        model_obj = json.loads(str(model_raw))
    except (json.JSONDecodeError, TypeError):
        return {"decision": "BLOCKED", "answer": "", "block_reason": "模型返回非有效JSON", "evidence_sources": evidence_sources, "steps": steps}

    if not isinstance(model_obj, dict):
        return {"decision": "BLOCKED", "answer": "", "block_reason": "模型返回非对象结构", "evidence_sources": evidence_sources, "steps": steps}

    decision = str(model_obj.get("decision", "")).upper()
    if decision not in ("PASS", "BLOCKED"):
        return {"decision": "BLOCKED", "answer": "", "block_reason": "模型返回无效decision字段", "evidence_sources": evidence_sources, "steps": steps}

    if decision == "BLOCKED":
        br = str(model_obj.get("block_reason") or "模型判断为BLOCKED")
        return {"decision": "BLOCKED", "answer": "", "block_reason": br, "evidence_sources": evidence_sources, "steps": steps}

    # PASS: answer always built from real evidence, never from model free-text
    final_answer = _build_answer(job_result, catalog_result)
    steps.append({"step": 4, "action": "model_decision", "result": "PASS"})

    return {"decision": "PASS", "answer": final_answer, "evidence_sources": evidence_sources, "steps": steps}
