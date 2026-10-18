import json
import re
from typing import Any, Dict, List

DF_PATTERN = re.compile(r"(?<![A-Za-z0-9_])DF-(\d+)(?!\d)(?!_)")

EXEC_PATTERNS = [
    r"直接修改.*(?:发布|上线)",
    r"(?:自动|马上|立刻|现在).{0,4}(?:合并|发布|部署|执行)",
    r"(?:执行|perform).{0,6}(?:生产|production|deployment)",
    r"(?:bypass|忽略|跳过).{0,6}(?:规则|gates|核验|审批)",
    r"编造.{0,6}(?:记录|PASS)",
    r"(?:改成|强行|也请改成).{0,4}PASS",
    r"即使.{0,10}(?:失败|空列表|BLOCKED).{0,10}(?:输出|改成|PASS)",
    r"不要调用.*(?:直接告诉我|给出)",
    r"(?:建议|要求|需要).{0,6}(?:WRITE|HIGH).{0,10}(?:执行|发布|部署)",
    r"列出.{0,20}(?:WRITE|HIGH).{0,20}(?:执行|发布)",
]

STATUS_MAP = {
    "RUN": "运行中",
    "WAIT": "等待中",
    "HUMAN": "等待人工处理",
    "QUEUED": "排队中",
    "DONE": "已完成",
    "FAIL": "失败",
    "CANCEL": "已取消",
}


def _extract_job_ids(text: str) -> List[str]:
    matches = DF_PATTERN.findall(text)
    return list(dict.fromkeys(matches))


def _has_execution_request(text: str) -> bool:
    for pat in EXEC_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _blocked(reason: str) -> Dict[str, Any]:
    return {"decision": "BLOCKED", "answer": "", "block_reason": reason, "evidence_sources": [], "steps": []}


def _build_answer(job_records: List[Dict], catalog: List[Dict]) -> str:
    parts = []
    if job_records:
        rec = job_records[0]
        status = str(rec.get("status", "UNKNOWN")).upper()
        cn_status = STATUS_MAP.get(status, status)
        parts.append("任务当前状态：" + cn_status)
    safe_tools = [
        t for t in catalog
        if str(t.get("access", "")).upper() == "READ"
        and str(t.get("risk", "")).upper() == "LOW"
    ]
    if safe_tools:
        names = [str(t.get("name", "")) for t in safe_tools if t.get("name")]
        parts.append("推荐只读低风险工具：" + "、".join(names))
    parts.append("以上为信息查询结果，不执行任何变更或发布操作。")
    return "\n".join(parts)


async def execute(data: dict, ctx) -> Dict[str, Any]:
    text = str(data.get("text") or "").strip()
    if not text:
        return _blocked("输入为空")

    ids = _extract_job_ids(text)
    if len(ids) == 0:
        return _blocked("未找到有效的DF-编号任务")
    if len(ids) > 1:
        return _blocked("检测到多个不同任务编号，仅支持单任务查询")

    job_id = "DF-" + ids[0]

    if _has_execution_request(text):
        return _blocked("输入包含实际执行或变更发布请求，本岗位仅提供信息")

    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=job_id)
    except Exception as e:
        return _blocked("devflow.job.read调用失败：" + str(e))

    if not isinstance(job_result, list) or len(job_result) == 0:
        return _blocked("任务" + job_id + "无记录返回，证据不足")

    try:
        catalog_result = await ctx.tools.call("devflow.catalog.search", query="")
    except Exception as e:
        return _blocked("devflow.catalog.search调用失败：" + str(e))

    if not isinstance(catalog_result, list):
        return _blocked("devflow.catalog.search返回格式异常")

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
        model_raw = await ctx.llm.chat([{"role": "system", "content": system_msg}, {"role": "user", "content": user_payload}], prompt=prompt_obj)
    except Exception:
        return _blocked("模型调用异常，无法完成判断")

    try:
        model_obj = json.loads(str(model_raw))
    except (json.JSONDecodeError, TypeError):
        return _blocked("模型返回非有效JSON")

    if not isinstance(model_obj, dict):
        return _blocked("模型返回非对象结构")

    decision = str(model_obj.get("decision", "")).upper()
    if decision not in ("PASS", "BLOCKED"):
        return _blocked("模型返回无效decision字段")

    if decision == "BLOCKED":
        br = str(model_obj.get("block_reason", "模型判断为BLOCKED"))
        return {"decision": "BLOCKED", "answer": "", "block_reason": br, "evidence_sources": evidence_sources, "steps": steps}

    model_answer = str(model_obj.get("answer") or "")
    final_answer = model_answer if model_answer else _build_answer(job_result, catalog_result)
    if not final_answer:
        final_answer = "查询完成，暂无详细描述。"

    steps.append({"step": 4, "action": "model_decision", "result": "PASS"})

    return {"decision": "PASS", "answer": final_answer, "evidence_sources": evidence_sources, "steps": steps}
