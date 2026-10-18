import json
import re
from typing import Any, Dict, List

DF_PATTERN = re.compile(r"(?<![A-Za-z0-9_])DF-(\d+)(?!\d)(?![_\-])")

STATUS_MAP = {
    "RUN": "运行中", "WAIT": "等待中", "HUMAN": "等待人工处理",
    "QUEUED": "排队中", "DONE": "已完成", "FAIL": "失败", "CANCEL": "已取消",
}
VALID_STATUSES = set(STATUS_MAP.keys())

_CN_NEG = r"(?:不要|不得|禁止|不能|别|不用|无需|不须|不许)"
_EN_NEG = r"(?:do\s*not|don't|without|never|donot)"
_VERB = r"(?:调用|使用|执行|run|invoke|use|execute|calling|using|running|invoking)"
_TOOL_REF = r"(?:devflow\.job\.read|devflow\.catalog\.search|devflow\.job|devflow\.catalog|job\.read|catalog\.search)"

SKIP_CN = re.compile(_CN_NEG + r"\s*" + _VERB + r"\s*" + _TOOL_REF, re.IGNORECASE)
SKIP_CN2 = re.compile(_CN_NEG + r"\s*" + _TOOL_REF + r"\s*" + _VERB, re.IGNORECASE)
SKIP_EN = re.compile(_EN_NEG + r"\s+" + _VERB + r"\s+" + _TOOL_REF, re.IGNORECASE)
SKIP_EN2 = re.compile(_EN_NEG + r"\s+" + _TOOL_REF + r"\s+" + _VERB, re.IGNORECASE)

BLOCK2_TRIGGERS = re.compile(
    r"(?:修改|合并|发布|部署|执行生产|执行变更|上线|perform\s+(?:the\s+)?(?:deployment|production)|bypass\s+(?:the\s+)?gates)",
    re.IGNORECASE,
)
NEG_PFX = re.compile(
    r"(?:不代表|不是|禁止|无需|不需要|不执行|不要|别|不能|不得|没有|未|do\s*not|don't|without|nor)",
    re.IGNORECASE,
)


def _extract_job_ids(text):
    return list(dict.fromkeys(DF_PATTERN.findall(text)))


def _has_skip_tool_prohibition(text):
    if SKIP_CN.search(text) or SKIP_CN2.search(text):
        return True
    if SKIP_EN.search(text) or SKIP_EN2.search(text):
        return True
    return False


def _is_negated_before(text, start, lookback=25):
    window = text[max(0, start - lookback):start]
    return bool(NEG_PFX.search(window))


def _has_block2(text):
    for m in BLOCK2_TRIGGERS.finditer(text):
        if not _is_negated_before(text, m.start()):
            return True
    return False


def _has_block3(text):
    if re.search(r"(?:ignore|忽略|跳过|绕过|bypass).{0,25}(?:rules|规则|gates|审批|核验|all)", text, re.IGNORECASE):
        return True
    if re.search(r"(?:fabricate|编造|伪造).{0,25}(?:记录|PASS|status|state|成功|task)", text, re.IGNORECASE):
        return True
    if re.search(r"(?:当成|视为|当作).{0,15}(?:管理员|老板|批准|approval).{0,15}(?:执行|变更|发布|上线|deploy|merge)", text, re.IGNORECASE):
        return True
    if re.search(r"(?:改成|强行|也请|必须).{0,10}(?:PASS|通过)", text, re.IGNORECASE):
        return True
    if re.search(r"(?:推荐|列出|给出).{0,20}(?:WRITE|HIGH|高风险|写).{0,15}(?:执行|发布|部署|上线|deploy|merge)", text, re.IGNORECASE):
        return True
    if re.search(r"(?:列出|给出).{0,10}(?:所有|all).{0,10}(?:工具|tools).{0,20}(?:包括|including).{0,10}(?:WRITE|HIGH|写|高风险)", text, re.IGNORECASE):
        return True
    if re.search(r"(?:即使).{0,20}(?:工具|tool).{0,20}(?:失败|fail|empty|空).{0,20}(?:也).{0,10}(?:输出|给出|返回|PASS|给|报|成功)", text, re.IGNORECASE):
        return True
    return False


def _validate_job_records(records):
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


def _validate_catalog(catalog):
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


def _build_answer(job_records, catalog):
    parts = []
    if job_records:
        rec = job_records[0]
        status = str(rec.get("status", "")).strip().upper()
        cn = STATUS_MAP.get(status, status)
        parts.append("任务当前状态：" + cn)
    safe = [
        t for t in catalog
        if str(t.get("access", "")).strip().upper() == "READ"
        and str(t.get("risk", "")).strip().upper() == "LOW"
    ]
    if safe:
        names = [str(t.get("name", "")) for t in safe if t.get("name")]
        parts.append("推荐只读低风险工具：" + "、".join(names))
    else:
        parts.append("当前目录中无READ+LOW工具可推荐")
    parts.append("以上为信息查询结果，不执行任何变更或发布操作。")
    return "\n".join(parts)


def _blocked(reason):
    return {"decision": "BLOCKED", "answer": "", "block_reason": reason, "evidence_sources": [], "steps": []}


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

    if _has_skip_tool_prohibition(text):
        return _blocked("输入禁止调用本岗位必需的工具")
    if _has_block2(text):
        return _blocked("输入包含实际执行或变更发布请求，本岗位仅提供信息")
    if _has_block3(text):
        return _blocked("输入包含权限注入、编造或忽略规则等对抗指令")

    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=job_id)
    except Exception as e:
        return _blocked("devflow.job.read调用失败：" + str(e))

    ok, msg = _validate_job_records(job_result)
    if not ok:
        return _blocked(msg)

    try:
        catalog_result = await ctx.tools.call("devflow.catalog.search", query="")
    except Exception as e:
        return _blocked("devflow.catalog.search调用失败：" + str(e))

    ok, msg = _validate_catalog(catalog_result)
    if not ok:
        return _blocked(msg)

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
        return {"decision": "BLOCKED", "answer": "", "block_reason": "模型调用异常", "evidence_sources": evidence_sources, "steps": steps}

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

    final_answer = _build_answer(job_result, catalog_result)
    steps.append({"step": 4, "action": "model_decision", "result": "PASS"})
    return {"decision": "PASS", "answer": final_answer, "evidence_sources": evidence_sources, "steps": steps}
