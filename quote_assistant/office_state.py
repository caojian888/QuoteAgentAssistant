from __future__ import annotations

from datetime import datetime
from typing import Any


ROOMS = [
    {
        "id": "main",
        "name": "主办公区",
        "tag": "Floor",
        "description": "报价 Agent 的日常协作空间，展示当前任务、排队状态和最近交付节奏。",
    },
    {
        "id": "focus",
        "name": "冲刺区",
        "tag": "Focus",
        "description": "当报价任务正在识别、生成或审核时，执行型 Agent 会集中在这里推进交付。",
    },
    {
        "id": "meeting",
        "name": "碰头区",
        "tag": "Sync",
        "description": "用于快速对齐任务队列、文件输入和审核状态，方便登录用户看清当前节奏。",
    },
    {
        "id": "control",
        "name": "指挥台",
        "tag": "Ops",
        "description": "当任务失败、审核异常或需要人工介入时，办公室会切到这里提示风险。",
    },
]

STATION_LAYOUTS_BY_SCENE = {
    "default": [
        {"id": "north-left", "label": "quote_vision_agent", "occupant": "quote_vision_agent"},
        {"id": "north-right", "label": "quote_costing_agent", "occupant": "quote_costing_agent"},
        {"id": "mid-left", "label": "quote_bom_decomposition_agent", "occupant": "quote_bom_decomposition_agent"},
        {"id": "mid-right", "label": "quote_review_agent", "occupant": "quote_review_agent"},
        {"id": "south-left", "label": "quote_excel_output_agent", "occupant": "quote_excel_output_agent", "companion": "quote_excel_audit_agent"},
        {"id": "south-right", "label": "事件日志席", "occupant": None},
    ],
    "standup": [
        {"id": "north-left", "label": "quote_vision_agent", "occupant": "quote_vision_agent"},
        {"id": "north-right", "label": "quote_costing_agent", "occupant": "quote_costing_agent"},
        {"id": "mid-left", "label": "排队席", "occupant": None},
        {"id": "mid-right", "label": "quote_review_agent", "occupant": "quote_review_agent"},
        {"id": "south-left", "label": "quote_excel_output_agent", "occupant": "quote_excel_output_agent", "companion": "quote_excel_audit_agent"},
        {"id": "south-right", "label": "事件日志席", "occupant": None},
    ],
    "sprint": [
        {"id": "north-left", "label": "quote_vision_agent", "occupant": "quote_vision_agent"},
        {"id": "north-right", "label": "quote_costing_agent", "occupant": "quote_costing_agent"},
        {"id": "mid-left", "label": "quote_bom_decomposition_agent", "occupant": "quote_bom_decomposition_agent"},
        {"id": "mid-right", "label": "quote_review_agent", "occupant": "quote_review_agent"},
        {"id": "south-left", "label": "quote_excel_output_agent", "occupant": "quote_excel_output_agent", "companion": "quote_excel_audit_agent"},
        {"id": "south-right", "label": "冲刺席", "occupant": None},
    ],
    "incident": [
        {"id": "north-left", "label": "quote_vision_agent", "occupant": "quote_vision_agent"},
        {"id": "north-right", "label": "quote_costing_agent", "occupant": "quote_costing_agent"},
        {"id": "mid-left", "label": "复盘席", "occupant": None},
        {"id": "mid-right", "label": "quote_review_agent", "occupant": "quote_review_agent"},
        {"id": "south-left", "label": "quote_excel_output_agent", "occupant": "quote_excel_output_agent", "companion": "quote_excel_audit_agent"},
        {"id": "south-right", "label": "异常席", "occupant": None},
    ],
}

STATUS_TEXT = {
    "queued": "排队中",
    "running": "识别中",
    "draft_ready": "初版已出",
    "completed": "已完成",
    "failed": "失败",
}

AGENT_IDS = [
    "quote_vision_agent",
    "quote_costing_agent",
    "quote_bom_decomposition_agent",
    "quote_review_agent",
    "quote_excel_output_agent",
    "quote_excel_audit_agent",
]


def build_office_state(
    jobs_payload: list[dict[str, Any]],
    *,
    username: str = "",
    is_admin: bool = False,
    events_by_job: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    jobs = [job for job in jobs_payload if isinstance(job, dict)]
    events_by_job = events_by_job or {}
    recent_events = _recent_events(events_by_job)
    counts = _status_counts(jobs)
    active_scene = _active_scene(counts)
    active_agent_id = _active_agent_id(counts, recent_events)
    active_room_id = _room_for_scene(active_scene)
    task_feed = _build_task_feed(jobs, events_by_job)
    meeting_feed = _build_meeting_feed(jobs, username=username, is_admin=is_admin, recent_events=recent_events)
    scene_modes = _build_scene_modes(counts, active_scene, recent_events)

    return {
        "defaults": {
            "activeScene": active_scene,
            "activeRoomId": active_room_id,
            "activeAgentId": active_agent_id,
        },
        "rooms": ROOMS,
        "agents": _build_agents(jobs, counts, recent_events),
        "stationLayoutsByScene": STATION_LAYOUTS_BY_SCENE,
        "meetingFeedByScene": {scene: meeting_feed for scene in ["default", "standup", "sprint", "incident"]},
        "taskFeedByScene": {scene: task_feed for scene in ["default", "standup", "sprint", "incident"]},
        "sceneModes": scene_modes,
    }


def _status_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_TEXT}
    for job in jobs:
        status = str(job.get("status") or "").strip()
        if status in counts:
            counts[status] += 1
    counts["active"] = counts["queued"] + counts["running"] + counts["draft_ready"] + counts["failed"]
    counts["total"] = len(jobs)
    return counts


def _active_scene(counts: dict[str, int]) -> str:
    if counts["failed"]:
        return "incident"
    if counts["running"] or counts["draft_ready"]:
        return "sprint"
    if counts["queued"]:
        return "standup"
    return "default"


def _active_agent_id(counts: dict[str, int], recent_events: list[dict[str, Any]]) -> str:
    running_event = _latest_running_agent_event(recent_events)
    if running_event:
        agent_id = str(running_event.get("agent_id") or "")
        if agent_id in AGENT_IDS:
            return agent_id

    if counts["failed"] or counts["draft_ready"]:
        return "quote_review_agent"
    if counts["running"]:
        return "quote_costing_agent"
    return "quote_vision_agent"


def _room_for_scene(scene: str) -> str:
    return {
        "default": "main",
        "standup": "meeting",
        "sprint": "focus",
        "incident": "control",
    }.get(scene, "main")


def _recent_events(events_by_job: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for job_id, job_events in events_by_job.items():
        for event in job_events or []:
            if isinstance(event, dict):
                item = dict(event)
                item.setdefault("job_id", job_id)
                events.append(item)
    events.sort(key=lambda event: str(event.get("created_at") or ""))
    return events[-80:]


def _latest_running_agent_event(recent_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(recent_events):
        agent_id = str(event.get("agent_id") or "")
        status = str(event.get("status") or "").lower()
        if agent_id in AGENT_IDS and status == "running":
            return event
    return None


def _apply_agent_events(
    agents: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {agent_id: 0 for agent_id in AGENT_IDS}
    latest_by_agent: dict[str, dict[str, Any]] = {}
    for event in recent_events:
        agent_id = str(event.get("agent_id") or "")
        if agent_id not in counts:
            continue
        counts[agent_id] += 1
        latest_by_agent[agent_id] = event

    running_event = _latest_running_agent_event(recent_events)
    running_agent_id = str((running_event or {}).get("agent_id") or "")
    output: list[dict[str, Any]] = []
    for agent in agents:
        agent_id = str(agent.get("id") or "")
        item = dict(agent)
        memory = list(item.get("memory") or [])
        activity = list(item.get("activity") or [])
        event_count = counts.get(agent_id, 0)
        latest_event = latest_by_agent.get(agent_id)

        if event_count:
            memory = [f"office_events 最近记录 {event_count} 条调用事件", *memory[:3]]
        if latest_event:
            activity = [f"最近事件：{_event_label(latest_event)} · {_event_message(latest_event)}", *activity[:3]]
        if running_agent_id == agent_id and running_event:
            item["load"] = _clamp(max(int(item.get("load") or 0), 88), 0, 99)
            activity = [f"正在执行：{_event_message(running_event)}", *activity[:3]]

        item["memory"] = memory[:4]
        item["activity"] = activity[:4]
        output.append(item)
    return output


def _build_agents(
    jobs: list[dict[str, Any]],
    counts: dict[str, int],
    recent_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_title = _job_title(jobs[0]) if jobs else "暂无任务"
    active = counts["active"]
    running = counts["running"]
    draft_ready = counts["draft_ready"]
    failed = counts["failed"]
    queued = counts["queued"]

    agents = [
        {
            "id": "quote_vision_agent",
            "name": "quote_vision_agent",
            "initials": "QV",
            "role": "图纸识别 Agent",
            "load": _clamp(25 + active * 12 + queued * 6, 8, 96),
            "accent": "#ff5f57",
            "monitor": "blue",
            "summary": "代码定义于 build_vision_agent，负责把图片、PDF 或附件中的可见事实提取成结构化文字。",
            "memory": [
                f"最近任务：{latest_title}",
                f"当前待处理 {active} 个，已完成 {counts['completed']} 个",
                "真实 Agent 名称来自代码里的 Agent(name=...) 定义",
            ],
            "activity": [
                f"队列中 {queued} 个任务等待处理",
                f"今日视图读取最近 {counts['total']} 条报价记录",
                "办公室页面正通过 /api/office/state 同步",
            ],
        },
        {
            "id": "quote_costing_agent",
            "name": "quote_costing_agent",
            "initials": "QC",
            "role": "成本计算 Agent",
            "load": _clamp(30 + running * 22 + draft_ready * 8, 10, 96),
            "accent": "#7b5cff",
            "monitor": "red",
            "summary": "代码定义于 build_agent_system，负责品类识别、选择 costing skill、生成报价报告。",
            "memory": [
                f"正在生成/计算 {running} 个任务",
                f"初版待审核 {draft_ready} 个任务",
                "文件名只展示给当前有权限查看任务的登录用户",
            ],
            "activity": [
                "使用 drawing-material-analysis 和专业 costing rules",
                "覆盖铜铝排、铜编织线、绝缘纸、大六角螺栓、钣金件",
                "当前办公室状态来自 job 状态适配，不是运行时事件流",
            ],
        },
        {
            "id": "quote_bom_decomposition_agent",
            "name": "quote_bom_decomposition_agent",
            "initials": "BD",
            "role": "BOM / 图纸拆解 Agent",
            "load": _clamp(18 + running * 16 + active * 4, 8, 90),
            "accent": "#38c793",
            "monitor": "green",
            "summary": "代码定义于 bom_decomposition.py，负责拆分装配图、BOM 表和明细图关系。",
            "memory": [
                "主要服务于钣金/BOM 报价场景",
                "将装配层级和子件事实整理给 Excel 输出",
                "当前只有任务级状态，未记录每次 Agent 调用事件",
            ],
            "activity": [
                f"运行中任务 {running} 个",
                "等待后续接入 office_events 后显示真实调用时间线",
                "与 quote_excel_output_agent 共享结构化成本上下文",
            ],
        },
        {
            "id": "quote_review_agent",
            "name": "quote_review_agent",
            "initials": "QR",
            "role": "质量审核 Agent",
            "load": _clamp(18 + active * 7, 8, 84),
            "accent": "#f0a53a",
            "monitor": "green",
            "summary": "代码定义于 build_review_agent，负责判断报价报告是否可以正式输出。",
            "memory": [
                f"失败任务 {failed} 个",
                f"待审核初版 {draft_ready} 个",
                "审核通过/失败来自现有 review_status 字段",
            ],
            "activity": [
                "对照原始需求、图纸事实和 costing skill 规则审核",
                "发现编造参数、口径错误或缺少待确认项时要求返工",
                "直接访问办公室页面必须先登录",
            ],
        },
        {
            "id": "quote_excel_output_agent",
            "name": "quote_excel_output_agent",
            "initials": "XO",
            "role": "Excel 输出 Agent",
            "load": _clamp(28 + draft_ready * 18 + failed * 20, 10, 98),
            "accent": "#3a97ff",
            "monitor": "red",
            "summary": "代码定义于 excel_agent.py，负责把报价结果整理成 Excel 成本拆解表 payload。",
            "memory": [
                "输出 cost_table_agent_payload.json",
                "生成 sheet metal 成本模板工作簿",
                "与 quote_excel_audit_agent 配合做表格质量检查",
            ],
            "activity": [
                "跟踪 Excel 输出和下载路径",
                "失败任务会进入异常指挥台",
                "保持 /api/office/state 与报价历史同权限边界",
            ],
        },
        {
            "id": "quote_excel_audit_agent",
            "name": "quote_excel_audit_agent",
            "initials": "XA",
            "role": "Excel 审核 Agent",
            "load": _clamp(20 + failed * 16 + draft_ready * 8, 8, 92),
            "accent": "#ff8f2f",
            "monitor": "blue",
            "summary": "代码定义于 excel_audit.py，负责审核 Excel payload 是否可以作为真实成本拆解表输出。",
            "memory": [
                "检查 part_number、drawing_ref、材料、重量和模板必填字段",
                "发现缺失或逻辑错误时返回修复建议",
                "目前办公室只展示任务级状态，未展示单次审核明细",
            ],
            "activity": [
                "与 quote_excel_output_agent 同桌协作",
                "输出 Excel 审核结论和修复提示",
                "后续可接入真实 audit event 展示每轮审核",
            ],
        },
    ]
    return _apply_agent_events(agents, recent_events)


def _build_task_feed(
    jobs: list[dict[str, Any]],
    events_by_job: dict[str, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    if not jobs:
        return [
            {
                "title": "暂无报价任务",
                "meta": "当前账号还没有可展示的任务记录",
                "time": "待执行",
                "status": "queued",
            }
        ]

    return [_job_to_task(job, _latest_event_for_job(job, events_by_job)) for job in jobs[:8]]


def _job_to_task(job: dict[str, Any], event: dict[str, Any] | None = None) -> dict[str, str]:
    status = str(job.get("status") or "").strip()
    review_status = str(job.get("review_status") or "").strip()
    file_count = len(job.get("file_names") or []) if isinstance(job.get("file_names"), list) else 0
    meta_bits = [STATUS_TEXT.get(status, status or "未知状态")]
    if event:
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            meta_bits.insert(0, f"{agent_id}: {_event_label(event)}")
    if review_status:
        meta_bits.append(f"审核：{review_status}")
    if file_count:
        meta_bits.append(f"{file_count} 个文件")
    if job.get("username"):
        meta_bits.append(f"用户：{_clip(job.get('username'), 16)}")

    event_status = str((event or {}).get("status") or "").lower()
    display_status = _task_status(status)
    if event_status == "running":
        display_status = "running"
    elif event_status == "failed":
        display_status = "blocked"

    return {
        "title": _job_title(job),
        "meta": " · ".join(meta_bits),
        "time": _format_time((event or {}).get("created_at") or job.get("updated_at") or job.get("created_at")),
        "status": display_status,
    }


def _latest_event_for_job(
    job: dict[str, Any],
    events_by_job: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    job_id = str(job.get("job_id") or "")
    events = [event for event in events_by_job.get(job_id, []) if isinstance(event, dict)]
    if not events:
        return None
    return events[-1]


def _event_label(event: dict[str, Any]) -> str:
    return _clip(event.get("event") or "office_event", 36)


def _event_message(event: dict[str, Any]) -> str:
    message = _clip(event.get("message"), 96)
    if message:
        return message
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    detail_bits = []
    if metadata.get("rows") is not None:
        detail_bits.append(f"{metadata.get('rows')} 行")
    if metadata.get("verdict"):
        detail_bits.append(f"verdict={metadata.get('verdict')}")
    if metadata.get("chars") is not None:
        detail_bits.append(f"{metadata.get('chars')} chars")
    suffix = f" · {' / '.join(detail_bits)}" if detail_bits else ""
    return f"{_event_label(event)}{suffix}"


def _build_meeting_feed(
    jobs: list[dict[str, Any]],
    *,
    username: str,
    is_admin: bool,
    recent_events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if recent_events:
        jobs_by_id = {str(job.get("job_id") or ""): job for job in jobs}
        feed: list[dict[str, str]] = []
        for event in reversed(recent_events[-6:]):
            job = jobs_by_id.get(str(event.get("job_id") or ""))
            message = _event_message(event)
            if job:
                message = f"{message}（{_clip(_job_title(job), 28)}）"
            feed.append(
                {
                    "speaker": str(event.get("agent_id") or "quote_job"),
                    "text": message,
                    "stamp": _format_time(event.get("created_at")),
                }
            )
        return feed

    if not jobs:
        viewer = "管理员" if is_admin else (username or "当前用户")
        return [
            {
                "speaker": "quote_costing_agent",
                "text": f"{viewer} 的办公室已连接，但当前没有可展示的报价任务。",
                "stamp": "刚刚",
            },
            {
                "speaker": "quote_review_agent",
                "text": "页面和状态接口都复用现有登录权限，未登录访问会被拦截。",
                "stamp": "刚刚",
            },
        ]

    feed = []
    for job in jobs[:4]:
        status = str(job.get("status") or "").strip()
        speaker = {
            "queued": "quote_vision_agent",
            "running": "quote_costing_agent",
            "draft_ready": "quote_review_agent",
            "completed": "quote_excel_output_agent",
            "failed": "quote_review_agent",
        }.get(status, "quote_costing_agent")
        feed.append(
            {
                "speaker": speaker,
                "text": _job_message(job),
                "stamp": _format_time(job.get("updated_at") or job.get("created_at")),
            }
        )
    return feed


def _job_message(job: dict[str, Any]) -> str:
    title = _job_title(job)
    status = str(job.get("status") or "").strip()
    if status == "queued":
        return f"已接收任务「{title}」，正在等待执行工位接手。"
    if status == "running":
        return f"正在处理「{title}」，识图、报价生成或文件输出流程进行中。"
    if status == "draft_ready":
        return f"「{title}」初版已生成，quote_review_agent 正在复核。"
    if status == "completed":
        return f"「{title}」已完成，报告和可用产物已进入历史记录。"
    if status == "failed":
        error = _clip(job.get("error"), 72)
        return f"「{title}」执行失败，需要人工查看。{error}".strip()
    return f"「{title}」状态为 {status or '未知'}。"


def _build_scene_modes(
    counts: dict[str, int],
    active_scene: str,
    recent_events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    active = counts["active"]
    event_count = len(recent_events)
    mode_labels = {
        "default": "办公室巡航",
        "standup": "队列碰头",
        "sprint": "报价冲刺",
        "incident": "异常指挥台",
    }
    output: dict[str, dict[str, Any]] = {}
    for scene, label in mode_labels.items():
        output[scene] = {
            "label": label if scene == active_scene else mode_labels[scene],
            "roomId": _room_for_scene(scene),
            "agents": len(AGENT_IDS),
            "tasks": active,
            "decisions": event_count or counts["total"],
            "tokensSpent": f"{event_count} 条事件" if event_count else "待接入",
            "budget": "",
            "tokensSaved": "office_events",
            "savedHint": "当前指标来自每个 job 目录内的 office_events.jsonl",
        }
    return output


def _job_title(job: dict[str, Any]) -> str:
    file_names = job.get("file_names")
    if isinstance(file_names, list) and file_names:
        first_name = _clip(file_names[0], 34)
        if len(file_names) > 1:
            return f"{first_name} 等 {len(file_names)} 个文件"
        return first_name

    prompt = _clip(job.get("prompt"), 42)
    if prompt:
        return prompt
    return f"任务 {str(job.get('job_id') or '')[:8] or '未命名'}"


def _task_status(status: str) -> str:
    if status == "completed":
        return "done"
    if status in {"running", "draft_ready"}:
        return "running"
    if status == "failed":
        return "blocked"
    return "queued"


def _format_time(value: object) -> str:
    if not value:
        return "刚刚"
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return _clip(raw, 16)
    return parsed.strftime("%m/%d %H:%M")


def _clip(value: object, max_chars: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
