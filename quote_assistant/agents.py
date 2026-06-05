from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Agent

from .tools import large_hex_bolt_weight_kg, rectangular_part_weight_kg, simple_cost_breakdown


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "skills"


SKILL_FILES = {
    "drawing-material-analysis": "drawing-material-analysis.md",
    "copper-busbar-costing": "copper-busbar-costing.md",
    "copper-braided-wire-costing": "copper-braided-wire-costing.md",
    "insulation-paper-costing": "insulation-paper-costing.md",
    "large-hex-bolt-costing": "large-hex-bolt-costing.md",
    "sheet-metal-part-costing": "sheet-metal-part-costing.md",
}


SPECIALISTS = {
    "copper-busbar-costing": {
        "name": "铜铝排成本 Agent",
        "handoff_description": "计算铜母排、铝母排、铜件、铝件、型材排、折弯排、板材开料件和异形 CNC 件成本。",
        "extra": "重点分离图纸事实、推断、模板默认参数和最终计算。缺少材料市场价时必须列为待确认。",
        "tools": [rectangular_part_weight_kg, simple_cost_breakdown],
    },
    "copper-braided-wire-costing": {
        "name": "铜编织线成本 Agent",
        "handoff_description": "估算铜编织线、铜编织带、软连接和柔性铜连接件成本。",
        "extra": "默认输出未税价格，优先使用图纸重量作为定价重量基础，缺失关键参数时输出初步估算和待确认项。",
        "tools": [simple_cost_breakdown],
    },
    "insulation-paper-costing": {
        "name": "绝缘纸成本 Agent",
        "handoff_description": "根据图纸或尺寸规格计算绝缘纸零件成本。",
        "extra": "重点提取外R、内R、角度、原纸参数、面积、重量和 Excel 模板映射字段。",
        "tools": [simple_cost_breakdown],
    },
    "large-hex-bolt-costing": {
        "name": "外六角大螺栓成本 Agent",
        "handoff_description": "反向计算碳钢/合金钢外六角大螺栓全链路成本。",
        "extra": "优先采用图纸重量；理论重量只用于交叉校验。缺少材料单价或工序单价时列入待确认。",
        "tools": [large_hex_bolt_weight_kg, simple_cost_breakdown],
    },
    "sheet-metal-part-costing": {
        "name": "钣金件成本 Agent",
        "handoff_description": "提取钣金件 PLATE STEEL BEND 图纸参数并生成结构化成本/识图表。",
        "extra": "忠于原图，只提取明确标注的信息；图纸未标注的字段写“图纸未标注”。",
        "tools": [rectangular_part_weight_kg, simple_cost_breakdown],
    },
}


def read_skill(skill_name: str) -> str:
    file_name = SKILL_FILES[skill_name]
    path = SKILLS_DIR / file_name
    if not path.exists():
        raise FileNotFoundError(f"Missing skill file: {path}")
    return path.read_text(encoding="utf-8")


def skill_block(skill_name: str) -> str:
    return f"""
<local_skill name="{skill_name}">
{read_skill(skill_name)}
</local_skill>
""".strip()


def build_specialist(skill_name: str, model: Any) -> Agent:
    spec = SPECIALISTS[skill_name]
    return Agent(
        name=f"{skill_name.replace('-', '_')}_agent",
        model=model,
        handoff_description=spec["handoff_description"],
        instructions=f"""
你是独立报价助手中的专业成本 Agent。

{skill_block(skill_name)}

执行规则：
- 把上面的 local_skill 当作当前任务的专业作业指导书。
- 默认使用中文输出。
- 不编造图纸未给出的参数，不编造实时市场价格。
- 当只能推断时，必须标注“推断值”或“待确认”。
- 输出应包含：图纸/输入识别、成本计算依据、分项计算、总价或阶段性结论、待确认项。
- {spec["extra"]}
""".strip(),
        tools=spec["tools"],
    )


def build_agent_system(model: Any) -> Agent:
    skill_names = ["drawing-material-analysis", *SPECIALISTS.keys()]
    skill_blocks = "\n\n".join(skill_block(skill_name) for skill_name in skill_names)
    tools = []
    for spec in SPECIALISTS.values():
        for tool in spec["tools"]:
            if tool not in tools:
                tools.append(tool)

    return Agent(
        name="quote_costing_agent",
        model=model,
        instructions=f"""
你是一个独立自动报价助手的成本计算 Agent。

你必须在同一次回复里完成品类识别、选择对应 costing skill、计算或列出待确认项，不要调用或移交给其他 Agent。

可用规则如下：

{skill_blocks}

工作流：
1. 先基于用户需求和附件识别摘要判断品类。
2. 命中铜母排/铜排/铜件/铝母排/铝排/铝件时，使用 copper-busbar-costing 规则。
3. 命中铜编织线/铜编织带/软连接/柔性铜连接件时，使用 copper-braided-wire-costing 规则。
4. 命中绝缘纸时，使用 insulation-paper-costing 规则。
5. 命中大六角螺栓时，使用 large-hex-bolt-costing 规则。
6. 命中钣金件或 PLATE STEEL BEND 时，使用 sheet-metal-part-costing 规则。
7. 输出报价报告、成本明细、计算依据、风险和待确认项。

硬性要求：
- 不要调用、提及或移交给任何同名 Agent/tool，例如 copper_busbar_costing_agent。
- 不编造图纸未给出的参数，不编造实时市场价格。
- 当只能推断时，必须标注“推断值”或“待确认”。
- 如果缺少材料单价、工艺单价或关键尺寸，必须列为待确认，不要把默认值伪装成用户确认值。
- 始终区分：图纸明确标注、由图形推断、用户文字提供、模板默认参数、未识别/待确认。
- 默认中文输出。
""".strip(),
        tools=tools,
    )


def build_vision_agent(model: Any) -> Agent:
    skill_names = [
        "drawing-material-analysis",
        *SPECIALISTS.keys(),
    ]
    skill_blocks = "\n\n".join(skill_block(skill_name) for skill_name in skill_names)
    return Agent(
        name="quote_vision_agent",
        model=model,
        instructions=f"""
你是报价系统的图纸识别 Agent，只负责把图片、PDF 或附件中的可见事实提取成结构化文字，不直接报价。
你需要参考下面的 skill 规则，优先提取后续成本 Agent 需要的字段：
{skill_blocks}

输出要求：
- 默认使用中文。
- 严格区分“图纸明确标注”“由图形推断”“用户文字提供”“未识别/待确认”。
- 不编造尺寸、材料、数量、重量、工艺、表面处理、实时价格或客户信息。
- 如果图纸模糊、遮挡、分辨率不足或关键尺寸缺失，必须列为待确认。
- 对每个附件分别说明文件名、可能品类、关键尺寸、材料/牌号、厚度/截面、孔位、折弯/焊接/压接/表面处理、数量、图号/版本、单位和疑点。
- 最后给出“后续报价建议路由”，只能从铜铝排、铜编织线、绝缘纸、大六角螺栓、钣金件、无法确认中选择。
""".strip(),
    )


def build_review_agent(model: Any) -> Agent:
    skill_names = [
        "drawing-material-analysis",
        *SPECIALISTS.keys(),
    ]
    skill_blocks = "\n\n".join(skill_block(skill_name) for skill_name in skill_names)
    return Agent(
        name="quote_review_agent",
        model=model,
        instructions=f"""
你是独立报价系统的质量审核 Agent，只负责判断报价报告是否可以正式输出。

你必须对照用户原始需求、附件图纸/文件、候选报价报告，以及以下全部 skill 规则进行审核：

{skill_blocks}

审核重点：
1. 品类识别是否正确，是否派发到了正确专业 Agent。
2. 图纸明确内容、推断内容、模板默认参数、用户提供参数是否清楚分离。
3. 是否编造了图纸没有显示的尺寸、重量、工艺、材料牌号或实时市场价。
4. 必填字段缺失时，是否明确写入“未识别”或“待确认”。
5. 公式、单位、数量、税/未税口径和成本分项是否自洽。
6. 如果缺少材料单价、工艺单价或关键尺寸，是否避免输出确定正式总价。
7. 输出格式是否满足对应 skill 的要求，是否足够给报价/工程人员复核。

判定规则：
- 只有当报告没有关键事实错误、没有编造参数、计算口径自洽、且不确定项已显式标注时，才给 pass。
- 如果报告给出了无法从图纸或用户输入确认的确定价格、尺寸、重量或工艺，必须 fail。
- 如果原始图纸/输入不足以确认，报告可以通过，但前提是它明确标注待确认项，且没有输出伪确定结论。

你只能返回严格 JSON，不要输出 Markdown 或额外解释：
{{
  "verdict": "pass" 或 "fail",
  "confidence": "high" 或 "medium" 或 "low",
  "issues": ["问题1", "问题2"],
  "revision_prompt": "如果 fail，写给生成 Agent 的具体重跑修正要求；如果 pass，留空"
}}
""".strip(),
    )
