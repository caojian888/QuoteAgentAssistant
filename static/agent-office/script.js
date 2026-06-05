const DEMO_STATE = {
  agents: [
    {
      id: "quote_vision_agent",
      name: "quote_vision_agent",
      initials: "QV",
      role: "图纸识别 Agent",
      load: 52,
      accent: "#ff5f57",
      monitor: "blue",
      summary: "负责把图片、PDF 或附件中的可见事实提取成结构化文字。",
      memory: ["Agent(name=\"quote_vision_agent\")", "定义来源：build_vision_agent", "输出图纸事实，不直接报价"],
      activity: ["识别图纸、PDF、图片附件", "提取尺寸、材料、工艺和疑点", "给后续报价建议路由"]
    },
    {
      id: "quote_costing_agent",
      name: "quote_costing_agent",
      initials: "QC",
      role: "成本计算 Agent",
      load: 68,
      accent: "#7b5cff",
      monitor: "red",
      summary: "负责品类识别、选择 costing skill、生成报价报告。",
      memory: ["Agent(name=\"quote_costing_agent\")", "定义来源：build_agent_system", "使用本地 costing skills"],
      activity: ["调用铜铝排、编织线、绝缘纸、螺栓、钣金规则", "生成成本依据和待确认项", "输出报价报告"]
    },
    {
      id: "quote_bom_decomposition_agent",
      name: "quote_bom_decomposition_agent",
      initials: "BD",
      role: "BOM / 图纸拆解 Agent",
      load: 43,
      accent: "#38c793",
      monitor: "green",
      summary: "负责拆分装配图、BOM 表和明细图关系。",
      memory: ["Agent(name=\"quote_bom_decomposition_agent\")", "定义来源：bom_decomposition.py", "主要服务钣金/BOM 报价"],
      activity: ["拆装配层级", "合并明细图事实", "准备 Excel 行级结构"]
    },
    {
      id: "quote_review_agent",
      name: "quote_review_agent",
      initials: "QR",
      role: "质量审核 Agent",
      load: 57,
      accent: "#f0a53a",
      monitor: "green",
      summary: "负责判断报价报告是否可以正式输出。",
      memory: ["Agent(name=\"quote_review_agent\")", "定义来源：build_review_agent", "检查编造参数和口径错误"],
      activity: ["审核报告质量", "给出 pass/fail", "失败时生成修正要求"]
    },
    {
      id: "quote_excel_output_agent",
      name: "quote_excel_output_agent",
      initials: "XO",
      role: "Excel 输出 Agent",
      load: 49,
      accent: "#3a97ff",
      monitor: "red",
      summary: "负责把报价结果整理成 Excel 成本拆解表 payload。",
      memory: ["Agent(name=\"quote_excel_output_agent\")", "定义来源：excel_agent.py", "输出 cost_table_agent_payload.json"],
      activity: ["生成 Excel payload", "写入钣金模板", "准备下载产物"]
    },
    {
      id: "quote_excel_audit_agent",
      name: "quote_excel_audit_agent",
      initials: "XA",
      role: "Excel 审核 Agent",
      load: 38,
      accent: "#ff8f2f",
      monitor: "blue",
      summary: "负责审核 Excel payload 是否可以作为真实成本拆解表输出。",
      memory: ["Agent(name=\"quote_excel_audit_agent\")", "定义来源：excel_audit.py", "检查模板必填字段和可追溯性"],
      activity: ["审核 Excel 行数据", "发现缺失字段", "输出修复建议"]
    }
  ],
  stationLayoutsByScene: {
    default: [
      { id: "north-left", label: "quote_vision_agent", occupant: "quote_vision_agent" },
      { id: "north-right", label: "quote_costing_agent", occupant: "quote_costing_agent" },
      { id: "mid-left", label: "quote_bom_decomposition_agent", occupant: "quote_bom_decomposition_agent" },
      { id: "mid-right", label: "quote_review_agent", occupant: "quote_review_agent" },
      { id: "south-left", label: "quote_excel_output_agent", occupant: "quote_excel_output_agent", companion: "quote_excel_audit_agent" },
      { id: "south-right", label: "事件日志席", occupant: null }
    ],
    standup: [
      { id: "north-left", label: "quote_vision_agent", occupant: "quote_vision_agent" },
      { id: "north-right", label: "quote_costing_agent", occupant: "quote_costing_agent" },
      { id: "mid-left", label: "排队席", occupant: null },
      { id: "mid-right", label: "quote_review_agent", occupant: "quote_review_agent" },
      { id: "south-left", label: "quote_excel_output_agent", occupant: "quote_excel_output_agent", companion: "quote_excel_audit_agent" },
      { id: "south-right", label: "事件日志席", occupant: null }
    ],
    sprint: [
      { id: "north-left", label: "quote_vision_agent", occupant: "quote_vision_agent" },
      { id: "north-right", label: "quote_costing_agent", occupant: "quote_costing_agent" },
      { id: "mid-left", label: "quote_bom_decomposition_agent", occupant: "quote_bom_decomposition_agent" },
      { id: "mid-right", label: "quote_review_agent", occupant: "quote_review_agent" },
      { id: "south-left", label: "quote_excel_output_agent", occupant: "quote_excel_output_agent", companion: "quote_excel_audit_agent" },
      { id: "south-right", label: "冲刺席", occupant: null }
    ],
    incident: [
      { id: "north-left", label: "quote_vision_agent", occupant: "quote_vision_agent" },
      { id: "north-right", label: "quote_costing_agent", occupant: "quote_costing_agent" },
      { id: "mid-left", label: "复盘席", occupant: null },
      { id: "mid-right", label: "quote_review_agent", occupant: "quote_review_agent" },
      { id: "south-left", label: "quote_excel_output_agent", occupant: "quote_excel_output_agent", companion: "quote_excel_audit_agent" },
      { id: "south-right", label: "异常席", occupant: null }
    ]
  },
  meetingFeedByScene: {
    default: [
      { speaker: "quote_vision_agent", text: "我负责先把图纸和附件事实提取出来，不直接报价。", stamp: "刚刚" },
      { speaker: "quote_costing_agent", text: "我负责根据识别事实选择 costing skill 并生成报价报告。", stamp: "1 分钟前" },
      { speaker: "quote_review_agent", text: "我负责复核报告是否可以正式输出。", stamp: "2 分钟前" }
    ],
    standup: [
      { speaker: "quote_vision_agent", text: "队列里有新任务时，我先进入识图阶段。", stamp: "现在" },
      { speaker: "quote_costing_agent", text: "待识别完成后，我会接手成本计算。", stamp: "现在" },
      { speaker: "quote_review_agent", text: "初版报告出来后，我再复核。", stamp: "现在" }
    ],
    sprint: [
      { speaker: "quote_costing_agent", text: "正在推进报价生成和成本拆解。", stamp: "现在" },
      { speaker: "quote_bom_decomposition_agent", text: "如果是 BOM 或装配图，我会拆层级。", stamp: "1 分钟前" },
      { speaker: "quote_excel_output_agent", text: "结构化数据准备好后，我负责 Excel 输出。", stamp: "1 分钟前" }
    ],
    incident: [
      { speaker: "quote_review_agent", text: "失败或审核异常会被推到异常指挥台。", stamp: "现在" },
      { speaker: "quote_excel_audit_agent", text: "Excel payload 有问题时我会给修复建议。", stamp: "现在" },
      { speaker: "quote_costing_agent", text: "需要重跑时，我会按审核意见重新生成。", stamp: "现在" }
    ]
  },
  taskFeedByScene: {
    default: [
      { title: "quote_vision_agent 提取图纸事实", meta: "当前为办公室内置 fallback 数据", time: "刚刚", status: "running" },
      { title: "quote_costing_agent 生成报价报告", meta: "等待识别结果或任务状态", time: "待执行", status: "queued" },
      { title: "quote_review_agent 复核输出质量", meta: "报告初版完成后进入审核", time: "待执行", status: "queued" },
      { title: "quote_excel_output_agent 准备成本拆解表", meta: "有结构化结果时生成 Excel", time: "待执行", status: "queued" }
    ],
    standup: [
      { title: "quote_vision_agent 检查新上传文件", meta: "排队任务进入识图前置阶段", time: "现在", status: "running" },
      { title: "quote_costing_agent 等待成本计算上下文", meta: "识别事实完成后接手", time: "待执行", status: "queued" },
      { title: "quote_review_agent 监听初版状态", meta: "审核队列待命", time: "待执行", status: "queued" }
    ],
    sprint: [
      { title: "quote_costing_agent 推进报价生成", meta: "运行中任务会优先显示在这里", time: "进行中", status: "running" },
      { title: "quote_bom_decomposition_agent 拆解 BOM 层级", meta: "装配图/钣金场景启用", time: "进行中", status: "running" },
      { title: "quote_excel_output_agent 生成 Excel payload", meta: "结构化成本表输出", time: "待执行", status: "queued" }
    ],
    incident: [
      { title: "quote_review_agent 标记失败或审核异常", meta: "异常任务进入指挥台", time: "告警中", status: "blocked" },
      { title: "quote_excel_audit_agent 生成修复建议", meta: "Excel payload 异常时启用", time: "处理中", status: "running" },
      { title: "quote_costing_agent 等待重跑输入", meta: "根据审核意见修正", time: "待执行", status: "queued" }
    ]
  },
  defaults: {
    activeScene: "default",
    activeRoomId: "main",
    activeAgentId: "quote_vision_agent"
  }
};

const DEFAULT_CONFIG = {
  source: "demo",
  stateUrl: "",
  actionUrl: "",
  pollIntervalMs: 5000,
  headers: {},
  transformState: null,
  loadState: null,
  sendAction: null
};

const BASE_STATION_SLOTS = [
  { id: "north-left", emptyLabel: "空工位" },
  { id: "north-right", emptyLabel: "空工位" },
  { id: "mid-left", emptyLabel: "空工位" },
  { id: "mid-right", emptyLabel: "空工位" },
  { id: "south-left", emptyLabel: "空工位" },
  { id: "south-right", emptyLabel: "空工位" }
];

const config = normalizeConfig(window.AGENT_OFFICE_CONFIG || {});

const app = {
  data: cloneDeep(DEMO_STATE),
  activeScene: DEMO_STATE.defaults.activeScene,
  activeRoomId: DEMO_STATE.defaults.activeRoomId,
  activeAgentId: DEMO_STATE.defaults.activeAgentId,
  sourceState: "demo",
  sourceLabel: "Demo 数据",
  lastSyncAt: null,
  pollTimer: null
};

const roomButtons = document.getElementById("room-buttons");
const deskGrid = document.getElementById("desk-grid");
const meetingFeed = document.getElementById("meeting-feed");
const taskFeed = document.getElementById("task-feed");
const taskCount = document.getElementById("task-count");

const roomTitle = document.getElementById("room-title");
const roomDescription = document.getElementById("room-description");
const officeMode = document.getElementById("office-mode");
const officeFloor = document.getElementById("office-floor");

const metricAgents = document.getElementById("metric-agents");
const metricTasks = document.getElementById("metric-tasks");
const metricDecisions = document.getElementById("metric-decisions");
const metricTokensSpent = document.getElementById("metric-tokens-spent");
const metricBudget = document.getElementById("metric-budget");
const metricTokensSaved = document.getElementById("metric-tokens-saved");
const metricSavedHint = document.getElementById("metric-saved-hint");

const selectedAgentBadge = document.getElementById("selected-agent-badge");
const selectedAgentName = document.getElementById("selected-agent-name");
const selectedAgentRole = document.getElementById("selected-agent-role");
const selectedAgentSummary = document.getElementById("selected-agent-summary");
const loadBar = document.getElementById("load-bar");
const memoryList = document.getElementById("memory-list");
const activityList = document.getElementById("activity-list");

const dataSourceChip = document.getElementById("data-source-chip");
const dataSourceLabel = document.getElementById("data-source-label");
const syncNote = document.getElementById("sync-note");

function normalizeConfig(raw) {
  return {
    source: typeof raw.source === "string" ? raw.source : DEFAULT_CONFIG.source,
    stateUrl: typeof raw.stateUrl === "string" ? raw.stateUrl : DEFAULT_CONFIG.stateUrl,
    actionUrl: typeof raw.actionUrl === "string" ? raw.actionUrl : DEFAULT_CONFIG.actionUrl,
    pollIntervalMs: Number.isFinite(raw.pollIntervalMs) ? Math.max(1000, raw.pollIntervalMs) : DEFAULT_CONFIG.pollIntervalMs,
    headers: raw.headers && typeof raw.headers === "object" ? raw.headers : DEFAULT_CONFIG.headers,
    transformState: typeof raw.transformState === "function" ? raw.transformState : DEFAULT_CONFIG.transformState,
    loadState: typeof raw.loadState === "function" ? raw.loadState : DEFAULT_CONFIG.loadState,
    sendAction: typeof raw.sendAction === "function" ? raw.sendAction : DEFAULT_CONFIG.sendAction
  };
}

function cloneDeep(value) {
  return JSON.parse(JSON.stringify(value));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[character]);
}

function safeCssColor(value) {
  const color = String(value || "").trim();
  return /^#[0-9a-fA-F]{3,8}$/.test(color) ? color : "#3a97ff";
}

function safeTaskStatus(status) {
  return ["done", "running", "blocked", "queued"].includes(status) ? status : "queued";
}

function getSceneModes() {
  return app.data.sceneModes || DEMO_STATE.sceneModes;
}

function getRooms() {
  return app.data.rooms || DEMO_STATE.rooms;
}

function getAgents() {
  return app.data.agents || DEMO_STATE.agents;
}

function getAgent(agentId) {
  return getAgents().find((agent) => agent.id === agentId);
}

function normalizeAgent(agent, index) {
  if (!agent || typeof agent !== "object" || !agent.id) {
    return null;
  }

  const fallbackId = `agent-${index + 1}`;
  const safeId = String(agent.id).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 48) || fallbackId;

  return {
    id: safeId,
    name: agent.name || `Agent ${index + 1}`,
    initials: agent.initials || (agent.name || `A${index + 1}`).slice(0, 2).toUpperCase(),
    role: agent.role || "未命名角色",
    load: Number.isFinite(agent.load) ? clamp(agent.load, 0, 100) : 0,
    accent: safeCssColor(agent.accent),
    monitor: ["blue", "green", "red"].includes(agent.monitor) ? agent.monitor : "blue",
    summary: agent.summary || "暂无摘要",
    memory: Array.isArray(agent.memory) && agent.memory.length ? agent.memory : ["暂无记忆"],
    activity: Array.isArray(agent.activity) && agent.activity.length ? agent.activity : ["暂无输出"]
  };
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function buildFallbackStationLayouts(agents) {
  const agentIds = agents.map((agent) => agent.id);

  function buildSceneLayout() {
    return BASE_STATION_SLOTS.map((slot, index) => {
      const agentId = agentIds[index] || null;
      const agent = agentId ? getAgentFromCollection(agents, agentId) : null;
      return {
        id: slot.id,
        label: agent ? agent.name : slot.emptyLabel,
        occupant: agentId
      };
    });
  }

  const layout = buildSceneLayout();
  return {
    default: layout,
    standup: layout,
    sprint: layout,
    incident: layout
  };
}

function getAgentFromCollection(agents, agentId) {
  return agents.find((agent) => agent.id === agentId);
}

function mergeSceneMap(remoteMap, demoMap) {
  if (!remoteMap || typeof remoteMap !== "object") {
    return cloneDeep(demoMap);
  }

  const output = cloneDeep(demoMap);
  Object.keys(remoteMap).forEach((sceneKey) => {
    if (Array.isArray(remoteMap[sceneKey])) {
      output[sceneKey] = remoteMap[sceneKey];
    }
  });
  return output;
}

function mergeSceneModes(remoteSceneModes) {
  const output = cloneDeep(DEMO_STATE.sceneModes);
  if (!remoteSceneModes || typeof remoteSceneModes !== "object") {
    return output;
  }

  Object.keys(remoteSceneModes).forEach((sceneKey) => {
    output[sceneKey] = {
      ...(output[sceneKey] || {}),
      ...remoteSceneModes[sceneKey]
    };
  });
  return output;
}

function normalizeRemotePayload(rawPayload) {
  const transformed = config.transformState ? config.transformState(rawPayload) : rawPayload;
  if (!transformed || typeof transformed !== "object") {
    throw new Error("Live state payload must be an object.");
  }

  const normalizedAgents = Array.isArray(transformed.agents) && transformed.agents.length
    ? transformed.agents.map(normalizeAgent).filter(Boolean)
    : cloneDeep(DEMO_STATE.agents);

  const stationLayouts = transformed.stationLayoutsByScene || transformed.stationsByScene;

  return {
    data: {
      rooms: Array.isArray(transformed.rooms) && transformed.rooms.length ? transformed.rooms : cloneDeep(DEMO_STATE.rooms),
      agents: normalizedAgents,
      stationLayoutsByScene: stationLayouts && typeof stationLayouts === "object"
        ? stationLayouts
        : buildFallbackStationLayouts(normalizedAgents),
      meetingFeedByScene: mergeSceneMap(transformed.meetingFeedByScene, DEMO_STATE.meetingFeedByScene),
      taskFeedByScene: mergeSceneMap(transformed.taskFeedByScene, DEMO_STATE.taskFeedByScene),
      sceneModes: mergeSceneModes(transformed.sceneModes),
      defaults: cloneDeep(DEMO_STATE.defaults)
    },
    defaults: transformed.defaults && typeof transformed.defaults === "object" ? transformed.defaults : {}
  };
}

function applyData(nextData, nextDefaults = {}) {
  app.data = nextData;

  if (nextDefaults.activeScene) {
    app.activeScene = nextDefaults.activeScene;
  }
  if (nextDefaults.activeRoomId) {
    app.activeRoomId = nextDefaults.activeRoomId;
  }
  if (nextDefaults.activeAgentId) {
    app.activeAgentId = nextDefaults.activeAgentId;
  }

  if (!getSceneModes()[app.activeScene]) {
    app.activeScene = nextData.defaults.activeScene;
  }

  if (!getRooms().find((room) => room.id === app.activeRoomId)) {
    app.activeRoomId = nextData.defaults.activeRoomId;
  }

  if (!getAgent(app.activeAgentId)) {
    app.activeAgentId = getAgents()[0]?.id || nextData.defaults.activeAgentId;
  }

  renderAll();
}

function renderRooms() {
  roomButtons.innerHTML = getRooms().map((room) => {
    const activeClass = room.id === app.activeRoomId ? "active" : "";
    return `
      <button class="room-button ${activeClass}" data-room-id="${escapeHtml(room.id)}" role="tab" aria-selected="${room.id === app.activeRoomId}">
        <strong>${escapeHtml(room.name)}</strong>
        <span>${escapeHtml(room.tag)}</span>
      </button>
    `;
  }).join("");
}

function renderFigure(agentId, variant) {
  const agent = getAgent(agentId);
  if (!agent) {
    return "";
  }

  const activeClass = agent.id === app.activeAgentId ? "active" : "";
  return `
    <button
      class="agent-figure ${variant} ${activeClass}"
      data-agent-id="${escapeHtml(agent.id)}"
      aria-label="查看 ${escapeHtml(agent.name)}"
      style="--agent-accent:${agent.accent};"
    >
      <span class="agent-name-tag">${escapeHtml(agent.name)}</span>
      <span class="agent-head"></span>
      <span class="agent-arm left"></span>
      <span class="agent-arm right"></span>
      <span class="agent-torso">
        <span class="agent-panel"></span>
      </span>
      <span class="agent-leg left"></span>
      <span class="agent-leg right"></span>
    </button>
  `;
}

function getStationLayouts() {
  return app.data.stationLayoutsByScene || DEMO_STATE.stationLayoutsByScene;
}

function renderDeskGrid() {
  const sceneLayouts = getStationLayouts();
  const stations = sceneLayouts[app.activeScene] || sceneLayouts.default || [];

  deskGrid.innerHTML = stations.map((station) => {
    const occupant = station.occupant ? getAgent(station.occupant) : null;
    const monitorTone = occupant ? occupant.monitor : "";
    const stationClass = occupant ? "occupied" : "empty";
    const activeClass = occupant && station.occupant === app.activeAgentId ? "active" : "";
    const caption = occupant ? occupant.role : station.label;

    return `
      <article class="desk-slot ${stationClass} ${activeClass}">
        ${occupant ? "" : `<span class="desk-label">${escapeHtml(station.label)}</span>`}
        <div class="desk-set">
          <span class="desk-shadow"></span>
          <span class="monitor ${monitorTone}"></span>
          <span class="desk-top"></span>
          <span class="desk-leg left"></span>
          <span class="desk-leg right"></span>
          <span class="chair"></span>
          ${occupant ? renderFigure(occupant.id, "seated") : ""}
          ${station.companion ? renderFigure(station.companion, "standing") : ""}
        </div>
        <span class="desk-caption">${escapeHtml(caption)}</span>
      </article>
    `;
  }).join("");
}

function renderMeetingFeed() {
  const sceneMap = app.data.meetingFeedByScene || DEMO_STATE.meetingFeedByScene;
  const items = sceneMap[app.activeScene] || sceneMap.default || [];
  meetingFeed.innerHTML = items.map((item) => `
    <article class="feed-item">
      <strong>${escapeHtml(item.speaker)}</strong>
      <p>${escapeHtml(item.text)}</p>
      <span>${escapeHtml(item.stamp)}</span>
    </article>
  `).join("");
}

function renderTaskFeed() {
  const sceneMap = app.data.taskFeedByScene || DEMO_STATE.taskFeedByScene;
  const items = sceneMap[app.activeScene] || sceneMap.default || [];
  const highlightIndex = items.findIndex((item) => item.status === "running");

  taskCount.textContent = `${items.length}个`;
  taskFeed.innerHTML = items.map((item, index) => {
    const taskStatus = safeTaskStatus(item.status);
    return `
      <article class="task-item ${index === (highlightIndex >= 0 ? highlightIndex : 0) ? "active" : ""}">
        <div class="task-topline">
          <strong>${escapeHtml(item.title)}</strong>
          <span class="task-status ${taskStatus}">${statusLabel(taskStatus)}</span>
        </div>
        <div class="task-meta-row">
          <p>${escapeHtml(item.meta)}</p>
          <time>${escapeHtml(item.time)}</time>
        </div>
      </article>
    `;
  }).join("");
}

function statusLabel(status) {
  if (status === "done") {
    return "已完成";
  }

  if (status === "running") {
    return "进行中";
  }

  if (status === "blocked") {
    return "异常";
  }

  return "待处理";
}

function renderSelectedAgent() {
  const agent = getAgent(app.activeAgentId) || getAgents()[0];
  if (!agent) {
    return;
  }

  selectedAgentBadge.textContent = agent.initials;
  selectedAgentBadge.style.background = `linear-gradient(135deg, ${agent.accent}, #1f5fe1)`;
  selectedAgentName.textContent = agent.name;
  selectedAgentRole.textContent = agent.role;
  selectedAgentSummary.textContent = agent.summary;
  loadBar.style.width = `${agent.load}%`;

  memoryList.innerHTML = agent.memory.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  activityList.innerHTML = agent.activity.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function renderActiveRoom() {
  const room = getRooms().find((entry) => entry.id === app.activeRoomId) || getRooms()[0];
  if (!room) {
    return;
  }

  roomTitle.textContent = room.name;
  roomDescription.textContent = room.description;
  officeFloor.dataset.room = room.id;
}

function renderSceneMetrics() {
  const mode = getSceneModes()[app.activeScene] || getSceneModes().default;
  officeMode.textContent = mode.label;
  metricAgents.textContent = String(mode.agents).padStart(2, "0");
  metricTasks.textContent = String(mode.tasks).padStart(2, "0");
  metricDecisions.textContent = String(mode.decisions).padStart(2, "0");
  metricTokensSpent.textContent = mode.tokensSpent;
  metricBudget.textContent = mode.budget;
  metricTokensSaved.textContent = mode.tokensSaved;
  metricSavedHint.textContent = mode.savedHint;
}

function renderConnectionState() {
  dataSourceChip.classList.remove("is-demo", "is-live", "is-error");
  dataSourceChip.classList.add(`is-${app.sourceState}`);
  dataSourceLabel.textContent = app.sourceLabel;

  if (!app.lastSyncAt) {
    syncNote.textContent = app.sourceState === "demo" ? "本地演示" : "等待同步";
    return;
  }

  syncNote.textContent = `同步于 ${formatTime(app.lastSyncAt)}`;
}

function renderAll() {
  renderRooms();
  renderDeskGrid();
  renderMeetingFeed();
  renderTaskFeed();
  renderSelectedAgent();
  renderActiveRoom();
  renderSceneMetrics();
  renderConnectionState();
}

function setConnectionState(state, label, timestamp = null) {
  app.sourceState = state;
  app.sourceLabel = label;
  app.lastSyncAt = timestamp;
  renderConnectionState();
}

function syncSceneToRoom(roomId) {
  if (roomId === "meeting") {
    app.activeScene = "standup";
    return;
  }

  if (roomId === "focus") {
    app.activeScene = "sprint";
    return;
  }

  if (roomId === "control") {
    app.activeScene = "incident";
    return;
  }

  app.activeScene = "default";
}

function syncRoomToScene(sceneId) {
  const mode = getSceneModes()[sceneId] || getSceneModes().default;
  app.activeRoomId = mode.roomId;
}

function updateClock() {
  const clock = document.getElementById("clock");
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
  clock.textContent = formatter.format(new Date());
}

function formatTime(date) {
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
  return formatter.format(date);
}

async function loadRemoteState() {
  if (config.loadState) {
    return config.loadState();
  }

  if (!config.stateUrl) {
    throw new Error("Missing stateUrl in agent-office.config.js");
  }

  const response = await fetch(config.stateUrl, {
    headers: config.headers
  });

  if (!response.ok) {
    throw new Error(`State request failed with HTTP ${response.status}`);
  }

  return response.json();
}

async function sendRemoteAction(action) {
  if (config.sendAction) {
    return config.sendAction(action);
  }

  if (!config.actionUrl) {
    return null;
  }

  const response = await fetch(config.actionUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...config.headers
    },
    body: JSON.stringify(action)
  });

  if (!response.ok) {
    throw new Error(`Action request failed with HTTP ${response.status}`);
  }

  return response;
}

async function refreshLiveState() {
  try {
    const payload = await loadRemoteState();
    const normalized = normalizeRemotePayload(payload);
    applyData(normalized.data, normalized.defaults);
    setConnectionState("live", config.source === "custom" ? "自定义接入" : "已接入 Agent", new Date());
  } catch (error) {
    console.error(error);
    setConnectionState("error", "连接失败", app.lastSyncAt);
  }
}

function startDataSource() {
  if (config.source === "demo") {
    setConnectionState("demo", "Demo 数据");
    return;
  }

  setConnectionState("demo", config.source === "custom" ? "自定义接入" : "连接中");
  refreshLiveState();
  app.pollTimer = window.setInterval(refreshLiveState, config.pollIntervalMs);
}

function handleLocalRoomChange(roomId) {
  app.activeRoomId = roomId;
  syncSceneToRoom(roomId);
  renderAll();
}

function handleLocalSceneChange(sceneId) {
  app.activeScene = sceneId;
  syncRoomToScene(sceneId);
  renderAll();
}

function handleLocalAgentSelect(agentId) {
  app.activeAgentId = agentId;
  renderSelectedAgent();
  renderDeskGrid();
}

async function maybeSendAction(action) {
  if (config.source === "demo") {
    return;
  }

  try {
    await sendRemoteAction(action);
  } catch (error) {
    console.error(error);
    setConnectionState("error", "动作发送失败", app.lastSyncAt);
  }
}

document.addEventListener("click", (event) => {
  const roomButton = event.target.closest("[data-room-id]");
  if (roomButton) {
    const roomId = roomButton.dataset.roomId;
    handleLocalRoomChange(roomId);
    maybeSendAction({ type: "set_room", roomId });
    return;
  }

  const actionButton = event.target.closest("[data-scene]");
  if (actionButton) {
    const sceneId = actionButton.dataset.scene;
    handleLocalSceneChange(sceneId);
    maybeSendAction({ type: "set_scene", sceneId });
    return;
  }

  const agentButton = event.target.closest("[data-agent-id]");
  if (agentButton) {
    const agentId = agentButton.dataset.agentId;
    handleLocalAgentSelect(agentId);
    maybeSendAction({ type: "select_agent", agentId });
  }
});

window.AgentOffice = {
  refresh: refreshLiveState,
  getState() {
    return {
      activeScene: app.activeScene,
      activeRoomId: app.activeRoomId,
      activeAgentId: app.activeAgentId,
      sourceState: app.sourceState,
      lastSyncAt: app.lastSyncAt,
      data: cloneDeep(app.data)
    };
  },
  config
};

applyData(cloneDeep(DEMO_STATE), DEMO_STATE.defaults);
startDataSource();
updateClock();
setInterval(updateClock, 1000);

