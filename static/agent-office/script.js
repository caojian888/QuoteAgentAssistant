const DEMO_STATE = {
  rooms: [
    {
      id: "main",
      name: "主办公区",
      tag: "Floor",
      description: "以工位为中心的日常协作空间，能一眼看清谁在工作、谁在待命、谁在排队等指令。"
    },
    {
      id: "focus",
      name: "冲刺区",
      tag: "Focus",
      description: "减少打扰，让执行型 Agent 长时间占据桌面推进交付，适合快节奏连贯输出。"
    },
    {
      id: "meeting",
      name: "碰头区",
      tag: "Sync",
      description: "用于快速对齐、讨论下一步和拆任务，像站会一样短促但信息密度高。"
    },
    {
      id: "control",
      name: "指挥台",
      tag: "Ops",
      description: "当故障、超预算或优先级突变时，所有注意力会回到这里重新调度。"
    }
  ],
  agents: [
    {
      id: "marvis",
      name: "Marvis",
      initials: "M",
      role: "总协调 / 前台接待",
      load: 68,
      accent: "#ff5f57",
      monitor: "blue",
      summary: "负责看大盘、分配工作、决定哪个 Agent 先上桌，保证办公室整体节奏顺滑。",
      memory: [
        "记得每个 Agent 最近一次切换上下文的时间",
        "掌握今天优先级最高的三个请求",
        "能判断什么时候要把任务从聊天切换成文件流"
      ],
      activity: [
        "把参考图风格同步给 File Agent",
        "把高优先级请求挪到右侧任务流顶部",
        "提醒 Thermes 控制 Token 消耗节奏"
      ]
    },
    {
      id: "file",
      name: "File Agent",
      initials: "F",
      role: "文档流 / 文件执行",
      load: 83,
      accent: "#7b5cff",
      monitor: "red",
      summary: "擅长处理文件、产物和结构化内容，是把草图落成真正交付物的主力工位。",
      memory: [
        "保存最近输出过的原型和文件命名规则",
        "知道每个页面产物在哪个目录里",
        "记录所有可直接交付给用户的版本快照"
      ],
      activity: [
        "刷新了原型页面布局和脚本",
        "生成了一张新预览图供人工验收",
        "准备把空间风格继续做得更像实体办公室"
      ]
    },
    {
      id: "computer",
      name: "Computer Agent",
      initials: "C",
      role: "终端执行 / 自动化",
      load: 57,
      accent: "#38c793",
      monitor: "green",
      summary: "负责点按钮、跑本地验证、看终端输出，让抽象设计变成可确认的真实结果。",
      memory: [
        "记住上一次本地截图的尺寸和校验路径",
        "知道哪些命令只需要读权限",
        "持续跟踪页面交互是否还联动正常"
      ],
      activity: [
        "用无头浏览器重新截了新版空间图",
        "验证了切场景和选 Agent 的联动",
        "保留了一套安全的本地验收路径"
      ]
    },
    {
      id: "thermes",
      name: "Thermes",
      initials: "T",
      role: "研究与检索",
      load: 49,
      accent: "#f0a53a",
      monitor: "green",
      summary: "负责找资料、补背景和压缩上下文，把零散信息整理成办公区可以消费的短内容。",
      memory: [
        "收藏了可复用的搜索关键词模板",
        "知道哪些来源适合直接给用户看",
        "能把长结果压成一句任务备注"
      ],
      activity: [
        "整理出参考图的主要视觉特征",
        "把展厅式布局拆成左中右三块",
        "补了一轮更接近实景办公室的命名"
      ]
    },
    {
      id: "sentry",
      name: "Sentry",
      initials: "S",
      role: "风控与回归检查",
      load: 74,
      accent: "#3a97ff",
      monitor: "red",
      summary: "盯布局风险、交互断点和视觉回归，确保每次改版后不会出现明显破相。",
      memory: [
        "记住最容易塌掉的是右侧面板与桌面比例",
        "知道移动端时 desk grid 最容易挤压",
        "持续监控哪些场景切换会让信息错位"
      ],
      activity: [
        "发现上一版左侧空间被拉得太高",
        "提醒重做为展厅式空间布局",
        "要求重验截图和点选结果"
      ]
    }
  ],
  stationLayoutsByScene: {
    default: [
      { id: "north-left", label: "Marvis", occupant: "marvis" },
      { id: "north-right", label: "空工位", occupant: null },
      { id: "mid-left", label: "空工位", occupant: null },
      { id: "mid-right", label: "空工位", occupant: null },
      { id: "south-left", label: "File Agent", occupant: "file", companion: "computer" },
      { id: "south-right", label: "空工位", occupant: null }
    ],
    standup: [
      { id: "north-left", label: "Marvis", occupant: "marvis" },
      { id: "north-right", label: "Thermes", occupant: "thermes" },
      { id: "mid-left", label: "碰头席", occupant: null },
      { id: "mid-right", label: "Sentry", occupant: "sentry" },
      { id: "south-left", label: "File Agent", occupant: "file", companion: "computer" },
      { id: "south-right", label: "空工位", occupant: null }
    ],
    sprint: [
      { id: "north-left", label: "Marvis", occupant: "marvis" },
      { id: "north-right", label: "Thermes", occupant: "thermes" },
      { id: "mid-left", label: "空工位", occupant: null },
      { id: "mid-right", label: "冲刺席", occupant: null },
      { id: "south-left", label: "File Agent", occupant: "file", companion: "computer" },
      { id: "south-right", label: "空工位", occupant: null }
    ],
    incident: [
      { id: "north-left", label: "Marvis", occupant: "marvis" },
      { id: "north-right", label: "Thermes", occupant: "thermes" },
      { id: "mid-left", label: "空工位", occupant: null },
      { id: "mid-right", label: "Sentry", occupant: "sentry" },
      { id: "south-left", label: "File Agent", occupant: "file", companion: "computer" },
      { id: "south-right", label: "故障席", occupant: null }
    ]
  },
  meetingFeedByScene: {
    default: [
      { speaker: "Marvis", text: "参考图已经确定方向，下一步重点是让空间更像真实办公室而不是卡片看板。", stamp: "刚刚" },
      { speaker: "File Agent", text: "页面结构已经换成左中右布局，工位和右侧任务流正在按实景感重新组织。", stamp: "1 分钟前" },
      { speaker: "Computer Agent", text: "截图验收会继续跟上，避免只是代码改了但真实画面不对劲。", stamp: "2 分钟前" }
    ],
    standup: [
      { speaker: "Marvis", text: "晨会开始，每个 Agent 只报一条目标和一条风险，避免站会变成长会。", stamp: "现在" },
      { speaker: "Thermes", text: "我负责继续比对参考图，让道具和桌面比例更接近你想要的感觉。", stamp: "现在" },
      { speaker: "Sentry", text: "我盯移动端挤压和右侧信息密度，防止新布局在小屏幕上塌掉。", stamp: "现在" }
    ],
    sprint: [
      { speaker: "File Agent", text: "进入冲刺后，我会优先稳定桌面层级、名字标签和角色站位。", stamp: "现在" },
      { speaker: "Computer Agent", text: "我压缩验证链路，只保留必要截图和关键点击检查。", stamp: "1 分钟前" },
      { speaker: "Marvis", text: "非关键问题先停一停，先把空间感和参考方向做准。", stamp: "1 分钟前" }
    ],
    incident: [
      { speaker: "Sentry", text: "故障演练已启动，如果 Token 超预算或工位任务堆积，会立即切回指挥台。", stamp: "现在" },
      { speaker: "Marvis", text: "我已经重新排了优先级，保留与参考图最相关的任务，其余延后。", stamp: "现在" },
      { speaker: "Thermes", text: "我会把高噪音任务剥离成备注，别让右侧列表失去可读性。", stamp: "现在" }
    ]
  },
  taskFeedByScene: {
    default: [
      { title: "帮我把虚拟办公室做成参考图那种实景感", meta: "预计 Token 消耗 11.4 万", time: "17:34 05/26", status: "done" },
      { title: "把旧版卡片式布局改成左中右展厅结构", meta: "预计 Token 消耗 6.8 万", time: "16:52 05/26", status: "done" },
      { title: "让 Marvis 和 File Agent 真正坐进工位里", meta: "预计 Token 消耗 4.1 万", time: "进行中", status: "running" },
      { title: "把右侧面板改成任务流而不是纯信息栏", meta: "预计 Token 消耗 3.9 万", time: "进行中", status: "running" },
      { title: "补一个更像实体展厅的左侧摆设区", meta: "预计 Token 消耗 2.7 万", time: "15:38 05/26", status: "done" },
      { title: "复验移动端和窄屏表现", meta: "预计 Token 消耗 2.3 万", time: "待执行", status: "blocked" }
    ],
    standup: [
      { title: "晨会确认今天只做空间感、工位感、任务流三件事", meta: "预计 Token 消耗 1.2 万", time: "现在", status: "running" },
      { title: "Thermes 对照参考图列视觉特征清单", meta: "预计 Token 消耗 3.6 万", time: "现在", status: "running" },
      { title: "Sentry 跟踪响应式风险", meta: "预计 Token 消耗 2.1 万", time: "现在", status: "running" },
      { title: "把不重要的小动效全部延后", meta: "预计 Token 消耗 0.8 万", time: "已同步", status: "done" }
    ],
    sprint: [
      { title: "把桌面、显示器、椅子和阴影做出层次", meta: "预计 Token 消耗 5.2 万", time: "进行中", status: "running" },
      { title: "让 Agent 点击选中与右侧详情联动", meta: "预计 Token 消耗 2.8 万", time: "进行中", status: "running" },
      { title: "压缩顶部控件，避免喧宾夺主", meta: "预计 Token 消耗 1.5 万", time: "14:16 05/26", status: "done" },
      { title: "补一轮截图验收", meta: "预计 Token 消耗 1.9 万", time: "待执行", status: "blocked" }
    ],
    incident: [
      { title: "右侧任务流过长，先保留最关键六条", meta: "预计 Token 消耗 1.3 万", time: "告警中", status: "blocked" },
      { title: "把中间办公区比例重新调平", meta: "预计 Token 消耗 4.4 万", time: "处理中", status: "running" },
      { title: "Sentry 复查每个场景的占位变化", meta: "预计 Token 消耗 2.2 万", time: "处理中", status: "running" },
      { title: "Marvis 重新排优先级并收敛工作面", meta: "预计 Token 消耗 0.9 万", time: "刚完成", status: "done" }
    ]
  },
  sceneModes: {
    default: {
      label: "办公室巡航",
      roomId: "main",
      agents: 5,
      tasks: 8,
      decisions: 8,
      tokensSpent: "0",
      budget: "/1000万",
      tokensSaved: "0",
      savedHint: "复用率持续提升"
    },
    standup: {
      label: "晨会模式",
      roomId: "meeting",
      agents: 5,
      tasks: 4,
      decisions: 10,
      tokensSpent: "12.6万",
      budget: "/1000万",
      tokensSaved: "2.3万",
      savedHint: "摘要复用提升 13%"
    },
    sprint: {
      label: "冲刺模式",
      roomId: "focus",
      agents: 5,
      tasks: 6,
      decisions: 5,
      tokensSpent: "32.7万",
      budget: "/1000万",
      tokensSaved: "5.8万",
      savedHint: "上下文压缩生效"
    },
    incident: {
      label: "故障演练",
      roomId: "control",
      agents: 5,
      tasks: 4,
      decisions: 12,
      tokensSpent: "48.1万",
      budget: "/1000万",
      tokensSaved: "4.4万",
      savedHint: "高噪音任务已隔离"
    }
  },
  defaults: {
    activeScene: "default",
    activeRoomId: "main",
    activeAgentId: "marvis"
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
        <span class="desk-label">${escapeHtml(station.label)}</span>
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
