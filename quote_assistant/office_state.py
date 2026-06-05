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
        {"id": "north-left", "label": "Marvis", "occupant": "marvis"},
        {"id": "north-right", "label": "Thermes", "occupant": "thermes"},
        {"id": "mid-left", "label": "待命席", "occupant": None},
        {"id": "mid-right", "label": "Sentry", "occupant": "sentry"},
        {"id": "south-left", "label": "File Agent", "occupant": "file", "companion": "computer"},
        {"id": "south-right", "label": "空工位", "occupant": None},
    ],
    "standup": [
        {"id": "north-left", "label": "Marvis", "occupant": "marvis"},
        {"id": "north-right", "label": "Thermes", "occupant": "thermes"},
        {"id": "mid-left", "label": "排队席", "occupant": None},
        {"id": "mid-right", "label": "Sentry", "occupant": "sentry"},
        {"id": "south-left", "label": "File Agent", "occupant": "file", "companion": "computer"},
        {"id": "south-right", "label": "空工位", "occupant": None},
    ],
    "sprint": [
        {"id": "north-left", "label": "Marvis", "occupant": "marvis"},
        {"id": "north-right", "label": "Thermes", "occupant": "thermes"},
        {"id": "mid-left", "label": "识别席", "occupant": None},
        {"id": "mid-right", "label": "审核席", "occupant": "sentry"},
        {"id": "south-left", "label": "File Agent", "occupant": "file", "companion": "computer"},
        {"id": "south-right", "label": "冲刺席", "occupant": None},
    ],
    "incident": [
        {"id": "north-left", "label": "Marvis", "occupant": "marvis"},
        {"id": "north-right", "label": "Thermes", "occupant": "thermes"},
        {"id": "mid-left", "label": "复盘席", "occupant": None},
        {"id": "mid-right", "label": "Sentry", "occupant": "sentry"},
        {"id": "south-left", "label": "File Agent", "occupant": "file", "companion": "computer"},
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


def build_office_state(
    jobs_payload: list[dict[str, Any]],
    *,
    username: str = "",
    is_admin: bool = False,
) -> dict[str, Any]:
    jobs = [job for job in jobs_payload if isinstance(job, dict)]
    counts = _status_counts(jobs)
    active_scene = _active_scene(counts)
    active_agent_id = _active_agent_id(counts)
    active_room_id = _room_for_scene(active_scene)
    task_feed = _build_task_feed(jobs)
    meeting_feed = _build_meeting_feed(jobs, username=username, is_admin=is_admin)
    scene_modes = _build_scene_modes(counts, active_scene)

    return {
        "defaults": {
            "activeScene": active_scene,
            "activeRoomId": active_room_id,
            "activeAgentId": active_agent_id,
        },
        "rooms": ROOMS,
        "agents": _build_agents(jobs, counts),
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


def _active_agent_id(counts: dict[str, int]) -> str:
    if counts["failed"] or counts["draft_ready"]:
        return "sentry"
    if counts["running"]:
        return "file"
    return "marvis"


def _room_for_scene(scene: str) -> str:
    return {
        "default": "main",
        "standup": "meeting",
        "sprint": "focus",
        "incident": "control",
    }.get(scene, "main")


def _build_agents(jobs: list[dict[str, Any]], counts: dict[str, int]) -> list[dict[str, Any]]:
    latest_title = _job_title(jobs[0]) if jobs else "暂无任务"
    active = counts["active"]
    running = counts["running"]
    draft_ready = counts["draft_ready"]
    failed = counts["failed"]
    queued = counts["queued"]

    return [
        {
            "id": "marvis",
            "name": "Marvis",
            "initials": "M",
            "role": "总协调 / 前台接待",
            "load": _clamp(25 + active * 12 + queued * 6, 8, 96),
            "accent": "#ff5f57",
            "monitor": "blue",
            "summary": "负责接收报价请求、观察队列状态，并把任务分配给文件、执行和审核工位。",
            "memory": [
                f"最近任务：{latest_title}",
                f"当前待处理 {active} 个，已完成 {counts['completed']} 个",
                "登录权限复用现有 Quote Agent Assistant 会话",
            ],
            "activity": [
                f"队列中 {queued} 个任务等待处理",
                f"今日视图读取最近 {counts['total']} 条报价记录",
                "办公室页面正通过 /api/office/state 同步",
            ],
        },
        {
            "id": "file",
            "name": "File Agent",
            "initials": "F",
            "role": "文档流 / 文件执行",
            "load": _clamp(30 + running * 22 + draft_ready * 8, 10, 96),
            "accent": "#7b5cff",
            "monitor": "red",
            "summary": "负责处理上传图纸、PDF 和图片，把输入文件转成可报价的结构化材料。",
            "memory": [
                f"正在识别 {running} 个任务",
                f"初版待审核 {draft_ready} 个任务",
                "文件名只展示给当前有权限查看任务的登录用户",
            ],
            "activity": [
                "同步最近报价文件列表",
                "跟踪识图、生成、Excel 输出阶段",
                "等待后续接入更细粒度 Agent 事件日志",
            ],
        },
        {
            "id": "computer",
            "name": "Computer Agent",
            "initials": "C",
            "role": "终端执行 / 自动化",
            "load": _clamp(20 + running * 18 + active * 5, 8, 92),
            "accent": "#38c793",
            "monitor": "green",
            "summary": "负责执行异步任务、轮询状态和把结果产物落到可下载路径。",
            "memory": [
                "任务状态来自现有 jobs 表和状态文件",
                "前端每 5 秒刷新一次办公室状态",
                "正式部署后会沿用同一路由",
            ],
            "activity": [
                f"运行中任务 {running} 个",
                "保持状态接口只读，暂不开放页面控制动作",
                "监听报价任务从 queued 到 completed 的流转",
            ],
        },
        {
            "id": "thermes",
            "name": "Thermes",
            "initials": "T",
            "role": "研究与检索",
            "load": _clamp(18 + active * 7, 8, 84),
            "accent": "#f0a53a",
            "monitor": "green",
            "summary": "负责把任务提示、报价需求和上下文压缩成办公室可快速理解的信息。",
            "memory": [
                "任务标题优先来自上传文件名，其次来自提示词摘要",
                "Token 指标当前未接入真实计量",
                "后续可接入检索、规则库和上下文复用统计",
            ],
            "activity": [
                "整理任务流摘要",
                "压缩长提示词避免右侧列表过载",
                "等待接入真实 Token 节省数据",
            ],
        },
        {
            "id": "sentry",
            "name": "Sentry",
            "initials": "S",
            "role": "风控与回归检查",
            "load": _clamp(28 + draft_ready * 18 + failed * 20, 10, 98),
            "accent": "#3a97ff",
            "monitor": "red",
            "summary": "负责盯审核状态、失败任务和需要人工介入的异常，把风险推到指挥台。",
            "memory": [
                f"失败任务 {failed} 个",
                f"待审核初版 {draft_ready} 个",
                "直接访问办公室页面必须先登录",
            ],
            "activity": [
                "检查失败和审核异常状态",
                "将异常任务推送到右侧任务流",
                "保持 /api/office/state 与报价历史同权限边界",
            ],
        },
    ]


def _build_task_feed(jobs: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not jobs:
        return [
            {
                "title": "暂无报价任务",
                "meta": "当前账号还没有可展示的任务记录",
                "time": "待接入",
                "status": "queued",
            }
        ]

    return [_job_to_task(job) for job in jobs[:8]]


def _job_to_task(job: dict[str, Any]) -> dict[str, str]:
    status = str(job.get("status") or "").strip()
    review_status = str(job.get("review_status") or "").strip()
    file_count = len(job.get("file_names") or []) if isinstance(job.get("file_names"), list) else 0
    meta_bits = [STATUS_TEXT.get(status, status or "未知状态")]
    if review_status:
        meta_bits.append(f"审核：{review_status}")
    if file_count:
        meta_bits.append(f"{file_count} 个文件")
    if job.get("username"):
        meta_bits.append(f"用户：{_clip(job.get('username'), 16)}")

    return {
        "title": _job_title(job),
        "meta": " · ".join(meta_bits),
        "time": _format_time(job.get("updated_at") or job.get("created_at")),
        "status": _task_status(status),
    }


def _build_meeting_feed(jobs: list[dict[str, Any]], *, username: str, is_admin: bool) -> list[dict[str, str]]:
    if not jobs:
        viewer = "管理员" if is_admin else (username or "当前用户")
        return [
            {
                "speaker": "Marvis",
                "text": f"{viewer} 的办公室已连接，但当前没有可展示的报价任务。",
                "stamp": "刚刚",
            },
            {
                "speaker": "Sentry",
                "text": "页面和状态接口都复用现有登录权限，未登录访问会被拦截。",
                "stamp": "刚刚",
            },
        ]

    feed = []
    for job in jobs[:4]:
        status = str(job.get("status") or "").strip()
        speaker = {
            "queued": "Marvis",
            "running": "File Agent",
            "draft_ready": "Sentry",
            "completed": "Marvis",
            "failed": "Sentry",
        }.get(status, "Marvis")
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
        return f"「{title}」初版已生成，审核 Agent 正在复核。"
    if status == "completed":
        return f"「{title}」已完成，报告和可用产物已进入历史记录。"
    if status == "failed":
        error = _clip(job.get("error"), 72)
        return f"「{title}」执行失败，需要人工查看。{error}".strip()
    return f"「{title}」状态为 {status or '未知'}。"


def _build_scene_modes(counts: dict[str, int], active_scene: str) -> dict[str, dict[str, Any]]:
    active = counts["active"]
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
            "agents": 5,
            "tasks": active,
            "decisions": counts["total"],
            "tokensSpent": "待接入",
            "budget": "",
            "tokensSaved": "待接入",
            "savedHint": "真实 Token 指标将在 Agent 事件日志接入后显示",
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
