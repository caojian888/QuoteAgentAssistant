const scenarios = {
  busbar: {
    status: "铜排链路运行中",
    subtitle: "优先检查材料截面、孔位与折弯展开长度。",
    confidence: "92%",
    agents: "6",
    time: "38s",
    output: "Excel + 报告",
    uploadTitle: "TMY-100x10 铜排总成图",
    uploadMeta: "3 页图纸，含孔位、材质和展开尺寸。",
    quotePrice: "¥ 428.60",
    quoteScore: "A-",
    material: "¥ 248.00",
    process: "¥ 126.40",
    extra: "¥ 54.20",
    risk: "低",
    resultTag: "Draft",
    summary: "系统判断为铜排件，关键孔径和厚度信息完整，适合直接生成报价草案。",
    pending: [
      "表面处理是否包含镀锡",
      "包装费用是否单列",
    ],
    stream: [
      ["00:04", "Vision Agent", "检测到铜排图纸，识别厚度 10mm、主孔径 13mm。"],
      ["00:11", "Routing Agent", "进入铜排成本技能，准备计算展开长度与损耗。"],
      ["00:21", "Costing Agent", "完成材料与折弯工序核算，废料抵扣已应用。"],
      ["00:33", "Review Agent", "未发现关键缺失项，建议生成 Excel 草稿。"],
    ],
  },
  sheet: {
    status: "钣金链路运行中",
    subtitle: "重点关注板厚、折弯次数与表面处理。",
    confidence: "89%",
    agents: "5",
    time: "44s",
    output: "报告 + 工艺清单",
    uploadTitle: "PLATE STEEL BEND 支架图",
    uploadMeta: "2 页图纸，包含折弯角度和喷粉要求。",
    quotePrice: "¥ 186.20",
    quoteScore: "B+",
    material: "¥ 92.60",
    process: "¥ 71.80",
    extra: "¥ 21.80",
    risk: "中",
    resultTag: "Review",
    summary: "系统识别为钣金折弯件，折弯和表面处理成本占比较高，建议人工确认喷粉等级。",
    pending: [
      "喷粉颜色是否为定制色",
      "边角去毛刺标准是否需升级",
    ],
    stream: [
      ["00:03", "Vision Agent", "识别到板厚 2.0mm，主工艺为激光开料 + 折弯。"],
      ["00:14", "Routing Agent", "切换到钣金件成本路径，提取折弯次数。"],
      ["00:24", "Costing Agent", "完成开料、折弯、喷粉费用拆分。"],
      ["00:39", "Review Agent", "发现表面处理等级缺失，保留为待确认项。"],
    ],
  },
  bolt: {
    status: "螺栓链路运行中",
    subtitle: "核查等级、热处理和表面处理是否齐全。",
    confidence: "95%",
    agents: "4",
    time: "29s",
    output: "报价卡 + 风险摘要",
    uploadTitle: "M24 外六角螺栓图",
    uploadMeta: "单页零件图，带性能等级与镀锌要求。",
    quotePrice: "¥ 32.80",
    quoteScore: "A",
    material: "¥ 14.20",
    process: "¥ 11.40",
    extra: "¥ 7.20",
    risk: "低",
    resultTag: "Ready",
    summary: "识别为大六角螺栓，规格与性能等级清晰，报价链路较短，可快速成稿。",
    pending: [
      "热处理炉次是否需追溯",
      "螺纹规验收标准是否客户指定",
    ],
    stream: [
      ["00:02", "Vision Agent", "识别规格 M24，性能等级 8.8，表面处理为镀锌。"],
      ["00:09", "Routing Agent", "进入大六角螺栓成本技能。"],
      ["00:17", "Costing Agent", "材料、车削、滚丝、热处理成本已拆分。"],
      ["00:26", "Output Agent", "报价卡生成完成，可导出交付。"],
    ],
  },
};

const state = {
  scenarioKey: "busbar",
  stepIndex: 0,
};

const chips = [...document.querySelectorAll(".scenario-chip")];
const stepCards = [...document.querySelectorAll(".step-card")];
const streamList = document.getElementById("stream-list");
const progressBar = document.getElementById("progress-bar");
const pipelineNote = document.getElementById("pipeline-note");
const startDemo = document.getElementById("start-demo");
const nextStep = document.getElementById("next-step");

function renderStream(items) {
  streamList.innerHTML = items
    .map(
      ([time, title, body]) => `
        <article class="stream-item">
          <span class="stream-time">${time}</span>
          <div>
            <strong>${title}</strong>
            <p>${body}</p>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderScenario() {
  const scenario = scenarios[state.scenarioKey];
  document.getElementById("hero-status").textContent = scenario.status;
  document.getElementById("hero-subtitle").textContent = scenario.subtitle;
  document.getElementById("metric-confidence").textContent = scenario.confidence;
  document.getElementById("metric-agents").textContent = scenario.agents;
  document.getElementById("metric-time").textContent = scenario.time;
  document.getElementById("metric-output").textContent = scenario.output;
  document.getElementById("upload-title").textContent = scenario.uploadTitle;
  document.getElementById("upload-meta").textContent = scenario.uploadMeta;
  document.getElementById("quote-price").textContent = scenario.quotePrice;
  document.getElementById("quote-score").textContent = scenario.quoteScore;
  document.getElementById("result-material").textContent = scenario.material;
  document.getElementById("result-process").textContent = scenario.process;
  document.getElementById("result-extra").textContent = scenario.extra;
  document.getElementById("result-risk").textContent = scenario.risk;
  document.getElementById("result-tag").textContent = scenario.resultTag;
  document.getElementById("result-summary").textContent = scenario.summary;
  document.getElementById("pending-list").innerHTML = scenario.pending.map((item) => `<li>${item}</li>`).join("");
  document.getElementById("stream-clock").textContent = scenario.stream.at(-1)?.[0] ?? "00:00";
  renderStream(scenario.stream);
  renderSteps();
}

function renderSteps() {
  const percent = ((state.stepIndex + 1) / stepCards.length) * 100;
  progressBar.style.width = `${percent}%`;
  pipelineNote.textContent = `进行到第 ${state.stepIndex + 1} 步`;
  stepCards.forEach((card, index) => {
    card.classList.remove("active", "done");
    if (index < state.stepIndex) {
      card.classList.add("done");
    } else if (index === state.stepIndex) {
      card.classList.add("active");
    }
  });
}

chips.forEach((chip) => {
  chip.addEventListener("click", () => {
    state.scenarioKey = chip.dataset.scenario;
    state.stepIndex = 0;
    chips.forEach((item) => item.classList.toggle("active", item === chip));
    renderScenario();
  });
});

startDemo.addEventListener("click", () => {
  state.stepIndex = stepCards.length - 1;
  renderSteps();
  pipelineNote.textContent = "模拟完成";
});

nextStep.addEventListener("click", () => {
  state.stepIndex = (state.stepIndex + 1) % stepCards.length;
  renderSteps();
});

renderScenario();
