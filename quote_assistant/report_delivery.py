from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    text: str = ""
    level: int = 0
    ordered: bool = False
    items: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()


def markdown_to_feishu_post_lines(markdown: str) -> list[list[dict[str, Any]]]:
    output: list[list[dict[str, Any]]] = []
    previous_blank = True
    for block in parse_markdown_blocks(markdown):
        if block.kind == "heading":
            if not previous_blank:
                output.append(feishu_text_line(" "))
            output.extend(wrap_feishu_text(strip_inline_markdown(block.text), bold=True, limit=520))
            previous_blank = False
        elif block.kind == "rule":
            output.append(feishu_text_line("────────────"))
            previous_blank = False
        elif block.kind == "table":
            if not previous_blank:
                output.append(feishu_text_line(" "))
            output.extend(table_to_feishu_lines(block.headers, block.rows))
            previous_blank = False
        elif block.kind == "list":
            for index, item in enumerate(block.items, start=1):
                prefix = f"{index}. " if block.ordered else "• "
                output.extend(wrap_feishu_text(prefix + strip_inline_markdown(item), limit=640))
            previous_blank = False
        elif block.kind == "quote":
            output.extend(wrap_feishu_text("│ " + strip_inline_markdown(block.text), limit=640))
            previous_blank = False
        elif block.kind == "code":
            if block.text.strip():
                output.extend(wrap_feishu_text(block.text.strip(), limit=680))
                previous_blank = False
        elif block.text.strip():
            output.extend(wrap_feishu_text(strip_inline_markdown(block.text), limit=680))
            previous_blank = False
        else:
            if output and not previous_blank:
                output.append(feishu_text_line(" "))
            previous_blank = True
    return trim_blank_post_lines(output)


def split_feishu_post_lines(
    lines: list[list[dict[str, Any]]],
    *,
    max_chars: int = 5200,
    max_lines: int = 90,
) -> list[list[list[dict[str, Any]]]]:
    chunks: list[list[list[dict[str, Any]]]] = []
    current: list[list[dict[str, Any]]] = []
    current_chars = 0
    for line in lines:
        line_chars = sum(len(str(item.get("text") or "")) for item in line)
        if current and (current_chars + line_chars > max_chars or len(current) >= max_lines):
            chunks.append(trim_blank_post_lines(current))
            current = []
            current_chars = 0
        current.append(line)
        current_chars += line_chars + 1
    if current:
        chunks.append(trim_blank_post_lines(current))
    return [chunk for chunk in chunks if chunk]


def render_markdown_report_pdf(markdown: str, output_path: Path, title: str = "报价报告") -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    regular_font, bold_font = register_pdf_fonts()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "QuoteBody",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=9.2,
        leading=15,
        textColor=colors.HexColor("#111827"),
        alignment=TA_LEFT,
        wordWrap="CJK",
        spaceAfter=5,
    )
    title_style = ParagraphStyle(
        "QuoteTitle",
        parent=body_style,
        fontName=bold_font,
        fontSize=18,
        leading=24,
        spaceAfter=10,
    )
    h1_style = ParagraphStyle("QuoteH1", parent=title_style, fontSize=15.5, leading=21, spaceBefore=9, spaceAfter=8)
    h2_style = ParagraphStyle("QuoteH2", parent=title_style, fontSize=13, leading=18, spaceBefore=7, spaceAfter=6)
    h3_style = ParagraphStyle("QuoteH3", parent=title_style, fontSize=11.2, leading=16, spaceBefore=5, spaceAfter=4)
    code_style = ParagraphStyle(
        "QuoteCode",
        parent=body_style,
        fontName=regular_font,
        fontSize=8.3,
        leading=12,
        backColor=colors.HexColor("#f6f7f9"),
        borderColor=colors.HexColor("#d9dee7"),
        borderWidth=0.4,
        borderPadding=5,
    )
    cell_style = ParagraphStyle(
        "QuoteCell",
        parent=body_style,
        fontSize=7.3,
        leading=10.6,
        spaceAfter=0,
        wordWrap="CJK",
    )
    header_cell_style = ParagraphStyle(
        "QuoteHeaderCell",
        parent=cell_style,
        fontName=bold_font,
        textColor=colors.HexColor("#111827"),
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=11 * mm,
        rightMargin=11 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
        title=title,
    )
    available_width = doc.width
    story: list[Any] = [Paragraph(escape_pdf_text(title), title_style)]

    for block in parse_markdown_blocks(markdown):
        if block.kind == "heading":
            style = h1_style if block.level <= 1 else h2_style if block.level == 2 else h3_style
            story.append(Paragraph(escape_pdf_text(strip_inline_markdown(block.text)), style))
        elif block.kind == "rule":
            story.append(Spacer(1, 3))
            story.append(HRFlowable(width="100%", color=colors.HexColor("#d9dee7"), thickness=0.7))
            story.append(Spacer(1, 5))
        elif block.kind == "table":
            table = build_pdf_table(block.headers, block.rows, header_cell_style, cell_style, available_width)
            if table is not None:
                story.append(table)
                story.append(Spacer(1, 8))
        elif block.kind == "list":
            list_flowables = []
            for index, item in enumerate(block.items, start=1):
                prefix = f"{index}. " if block.ordered else "• "
                list_flowables.append(Paragraph(escape_pdf_text(prefix + strip_inline_markdown(item)), body_style))
            story.append(KeepTogether(list_flowables))
            story.append(Spacer(1, 3))
        elif block.kind == "quote":
            story.append(Paragraph(escape_pdf_text("│ " + strip_inline_markdown(block.text)), body_style))
        elif block.kind == "code":
            story.append(Paragraph(escape_pdf_text(block.text).replace("\n", "<br/>"), code_style))
            story.append(Spacer(1, 5))
        elif block.text.strip():
            story.append(Paragraph(escape_pdf_text(strip_inline_markdown(block.text)), body_style))

    def draw_footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont(regular_font, 7.5)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawRightString(document.pagesize[0] - document.rightMargin, 6.5 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return output_path


def parse_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    lines = normalize_markdown(markdown).split("\n")
    blocks: list[MarkdownBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        trimmed = line.strip()
        if not trimmed:
            blocks.append(MarkdownBlock(kind="blank"))
            index += 1
            continue
        if trimmed.startswith("```"):
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(MarkdownBlock(kind="code", text="\n".join(code_lines)))
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", trimmed)
        if heading:
            blocks.append(MarkdownBlock(kind="heading", text=heading.group(2).strip(), level=len(heading.group(1))))
            index += 1
            continue
        if re.match(r"^([-*_])\s*\1\s*\1\s*$", trimmed):
            blocks.append(MarkdownBlock(kind="rule"))
            index += 1
            continue
        if is_table_start(lines, index):
            headers = tuple(split_table_row(lines[index]))
            index += 2
            rows: list[tuple[str, ...]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(tuple(split_table_row(lines[index])))
                index += 1
            blocks.append(MarkdownBlock(kind="table", headers=headers, rows=tuple(rows)))
            continue
        if re.match(r"^\s*>", line):
            quote_lines: list[str] = []
            while index < len(lines) and re.match(r"^\s*>", lines[index]):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[index]).strip())
                index += 1
            blocks.append(MarkdownBlock(kind="quote", text=" ".join(quote_lines)))
            continue
        list_match = re.match(r"^\s*(\d+[.)]|[-*+])\s+(.+)$", line)
        if list_match:
            ordered = bool(re.match(r"\d+[.)]", list_match.group(1)))
            items: list[str] = []
            while index < len(lines):
                current = lines[index]
                current_match = re.match(r"^\s*(\d+[.)]|[-*+])\s+(.+)$", current)
                if not current_match:
                    break
                current_ordered = bool(re.match(r"\d+[.)]", current_match.group(1)))
                if current_ordered != ordered:
                    break
                items.append(current_match.group(2).strip())
                index += 1
            blocks.append(MarkdownBlock(kind="list", ordered=ordered, items=tuple(items)))
            continue

        paragraph_lines = [trimmed]
        index += 1
        while index < len(lines) and is_paragraph_continuation(lines, index):
            paragraph_lines.append(lines[index].strip())
            index += 1
        blocks.append(MarkdownBlock(kind="paragraph", text=" ".join(paragraph_lines)))
    return collapse_blank_blocks(blocks)


def normalize_markdown(markdown: str) -> str:
    return (markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def collapse_blank_blocks(blocks: Iterable[MarkdownBlock]) -> list[MarkdownBlock]:
    collapsed: list[MarkdownBlock] = []
    previous_blank = True
    for block in blocks:
        if block.kind == "blank":
            if not previous_blank:
                collapsed.append(block)
            previous_blank = True
            continue
        collapsed.append(block)
        previous_blank = False
    while collapsed and collapsed[-1].kind == "blank":
        collapsed.pop()
    return collapsed


def is_paragraph_continuation(lines: list[str], index: int) -> bool:
    if index >= len(lines):
        return False
    line = lines[index]
    trimmed = line.strip()
    if not trimmed:
        return False
    if trimmed.startswith("```") or re.match(r"^(#{1,6})\s+(.+)$", trimmed):
        return False
    if re.match(r"^([-*_])\s*\1\s*\1\s*$", trimmed):
        return False
    if is_table_start(lines, index):
        return False
    if re.match(r"^\s*(\d+[.)]|[-*+])\s+(.+)$", line) or re.match(r"^\s*>", line):
        return False
    return True


def split_table_row(row: str) -> list[str]:
    value = row.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [clean_table_cell(cell) for cell in value.split("|")]


def clean_table_cell(value: str) -> str:
    return strip_inline_markdown(value.replace("<br>", "\n").replace("<br/>", "\n").strip())


def is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and is_table_separator(lines[index + 1])


def is_table_separator(row: str) -> bool:
    cells = split_table_row(row)
    if not cells:
        return False
    return all(re.match(r"^:?-{3,}:?$", cell.replace(" ", "")) for cell in cells)


def strip_inline_markdown(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)\*(?!\s)(.*?)(?<!\s)\*(?!\w)", r"\1", cleaned)
    cleaned = cleaned.replace("`", "")
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def feishu_text_line(text: str, *, bold: bool = False) -> list[dict[str, Any]]:
    element: dict[str, Any] = {"tag": "text", "text": text or " "}
    if bold:
        element["style"] = ["bold"]
    return [element]


def wrap_feishu_text(text: str, *, bold: bool = False, limit: int = 680) -> list[list[dict[str, Any]]]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    return [feishu_text_line(part, bold=bold) for part in split_readable_text(cleaned, limit)]


def split_readable_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    punctuation = "。；;，,、 "
    while len(remaining) > limit:
        cut = max(remaining.rfind(mark, 0, limit) for mark in punctuation)
        if cut < limit * 0.45:
            cut = limit
        elif cut < len(remaining):
            cut += 1
        piece = remaining[:cut].strip()
        if piece:
            parts.append(piece)
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def table_to_feishu_lines(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> list[list[dict[str, Any]]]:
    output: list[list[dict[str, Any]]] = []
    if not headers:
        return output
    table_name = "表格明细：" + " / ".join(header for header in headers if header)[:120]
    output.append(feishu_text_line(table_name, bold=True))
    if not rows:
        output.append(feishu_text_line("暂无数据"))
        return output
    for row in rows:
        cells = pad_cells(row, len(headers))
        output.append(feishu_text_line(table_row_title(headers, cells), bold=True))
        for header, cell in zip(headers, cells):
            if not cell:
                continue
            label = header or "字段"
            output.extend(wrap_feishu_text(f"{label}：{cell}", limit=620))
        output.append(feishu_text_line(" "))
    return output


def pad_cells(row: tuple[str, ...], size: int) -> tuple[str, ...]:
    if len(row) >= size:
        return row[:size]
    return row + tuple("" for _ in range(size - len(row)))


def table_row_title(headers: tuple[str, ...], row: tuple[str, ...]) -> str:
    index_value = first_cell_by_header(headers, row, ("序号", "编号", "no", "No", "NO"))
    name_value = first_cell_by_header(
        headers,
        row,
        ("品名/描述", "品名", "名称", "项目", "对象", "文件", "图号/料号", "图号", "料号"),
    )
    if index_value and name_value:
        return f"{index_value}）{name_value}"
    if name_value:
        return name_value
    if index_value:
        return f"第 {index_value} 项"
    first_value = next((cell for cell in row if cell), "明细项")
    return first_value[:80]


def first_cell_by_header(headers: tuple[str, ...], row: tuple[str, ...], names: tuple[str, ...]) -> str:
    normalized_headers = [(header.strip().lower(), cell.strip()) for header, cell in zip(headers, row)]
    for name in names:
        target = name.lower()
        for normalized, cell in normalized_headers:
            if normalized == target and cell:
                return cell
    for name in names:
        target = name.lower()
        if len(target) <= 1:
            continue
        for normalized, cell in normalized_headers:
            if target in normalized and cell:
                return cell
    return ""


def trim_blank_post_lines(lines: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    trimmed = list(lines)
    while trimmed and line_text(trimmed[0]).strip() == "":
        trimmed.pop(0)
    while trimmed and line_text(trimmed[-1]).strip() == "":
        trimmed.pop()
    return trimmed


def line_text(line: list[dict[str, Any]]) -> str:
    return "".join(str(item.get("text") or "") for item in line)


def escape_pdf_text(text: str) -> str:
    return html.escape(text or " ").replace("\n", "<br/>")


def build_pdf_table(
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    header_style: Any,
    cell_style: Any,
    available_width: float,
) -> Any | None:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    if not headers:
        return None
    column_count = len(headers)
    data: list[list[Any]] = [[Paragraph(escape_pdf_text(header or " "), header_style) for header in headers]]
    for row in rows:
        data.append([Paragraph(escape_pdf_text(cell or " "), cell_style) for cell in pad_cells(row, column_count)])

    table = Table(data, colWidths=table_column_widths(headers, rows, available_width), repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9dee7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def table_column_widths(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...], available_width: float) -> list[float]:
    weights: list[float] = []
    for index, header in enumerate(headers):
        values = [header]
        values.extend(row[index] for row in rows[:20] if index < len(row))
        longest = max((display_width(value) for value in values), default=8)
        if header.strip() in {"序号", "编号", "No", "NO", "no"}:
            weight = 5
        elif any(label in header for label in ("品名", "描述", "计价", "报价", "备注")):
            weight = min(max(longest, 13), 24)
        elif any(label in header for label in ("文件", "图号", "料号")):
            weight = min(max(longest, 10), 20)
        else:
            weight = min(max(longest, 8), 18)
        weights.append(float(weight))
    total = sum(weights) or 1.0
    return [available_width * weight / total for weight in weights]


def display_width(text: str) -> int:
    width = 0
    for character in str(text or ""):
        width += 2 if ord(character) > 127 else 1
    return width


def register_pdf_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    regular_candidates = [
        os.getenv("QUOTE_REPORT_PDF_FONT", "").strip(),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf",
        r"C:\Windows\Fonts\SourceHanSansCN-Normal.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    bold_candidates = [
        os.getenv("QUOTE_REPORT_PDF_BOLD_FONT", "").strip(),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Bold.otf",
        r"C:\Windows\Fonts\Noto Sans SC Bold (TrueType).otf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]

    regular_path = first_existing_path(regular_candidates)
    if regular_path:
        try:
            register_ttf_font("QuoteReportRegular", regular_path)
            bold_path = first_existing_path(bold_candidates)
            if bold_path:
                try:
                    register_ttf_font("QuoteReportBold", bold_path)
                    return "QuoteReportRegular", "QuoteReportBold"
                except Exception:
                    pass
            return "QuoteReportRegular", "QuoteReportRegular"
        except Exception:
            pass

    font_name = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    except Exception:
        pass
    return font_name, font_name


def first_existing_path(candidates: Iterable[str]) -> str:
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def register_ttf_font(name: str, path: str) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
    except TypeError:
        pdfmetrics.registerFont(TTFont(name, path))
