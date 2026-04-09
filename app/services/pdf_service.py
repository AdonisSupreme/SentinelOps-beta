"""
PDF Generation Service for SentinelOps checklist instances.
Builds polished operational reports that match the product's brand language.
"""

import io
import os
import re
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
    return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw[:19], fmt)
                except ValueError:
                    continue
    return None


def _sanitize_filename_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_")
    return cleaned or "Checklist"


def _format_filename_date(value: Any) -> str:
    parsed = _parse_date(value)
    if parsed:
        return parsed.strftime("%d_%m_%Y")
    return _sanitize_filename_component(str(value or datetime.now().date()))


def _build_shift_filename_label(shift: Any, template_name: Optional[str] = None) -> str:
    normalized_shift = str(shift or "").strip().upper()
    shift_labels = {
        "MORNING": "Morning_Shift",
        "AFTERNOON": "Afternoon_Shift",
        "NIGHT": "Night_Shift",
    }
    if normalized_shift in shift_labels:
        return shift_labels[normalized_shift]
    if template_name:
        return _sanitize_filename_component(template_name)
    return "Checklist"


def build_checklist_pdf_filename(instance_data: Dict[str, Any]) -> str:
    shift_label = _build_shift_filename_label(
        instance_data.get("shift"),
        instance_data.get("template_name"),
    )
    date_label = _format_filename_date(instance_data.get("checklist_date"))
    return f"SentinelOps_{shift_label}_{date_label}.pdf"


class SentinelOpsPDFGenerator:
    """Branded PDF generator for SentinelOps checklist instances."""

    COLORS = {
        "ink": HexColor("#0f172a"),
        "ink_soft": HexColor("#1e293b"),
        "muted": HexColor("#475569"),
        "muted_soft": HexColor("#64748b"),
        "border": HexColor("#cbd5e1"),
        "border_soft": HexColor("#e2e8f0"),
        "surface": HexColor("#ffffff"),
        "surface_alt": HexColor("#f8fafc"),
        "surface_tint": HexColor("#f1f5f9"),
        "hero": HexColor("#0b1220"),
        "hero_mid": HexColor("#12233d"),
        "blue": HexColor("#2563eb"),
        "blue_soft": HexColor("#dbeafe"),
        "sky": HexColor("#38bdf8"),
        "green": HexColor("#22c55e"),
        "green_soft": HexColor("#dcfce7"),
        "warning": HexColor("#f59e0b"),
        "warning_soft": HexColor("#fef3c7"),
        "danger": HexColor("#ef4444"),
        "danger_soft": HexColor("#fee2e2"),
    }

    def __init__(self):
        self.margin_left = 42
        self.margin_right = 42
        self.margin_top = 56
        self.margin_bottom = 42
        self.content_width = A4[0] - self.margin_left - self.margin_right
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        self.styles.add(ParagraphStyle(
            name="ReportEyebrow",
            parent=self.styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10,
            textColor=HexColor("#94a3b8"),
            alignment=TA_LEFT,
            spaceAfter=6,
        ))

        self.styles.add(ParagraphStyle(
            name="HeroTitle",
            parent=self.styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=self.COLORS["ink"],
            alignment=TA_LEFT,
            spaceAfter=8,
        ))

        self.styles.add(ParagraphStyle(
            name="HeroSubtitle",
            parent=self.styles["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=self.COLORS["muted"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="SectionTitle",
            parent=self.styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=self.COLORS["ink"],
            alignment=TA_LEFT,
            spaceBefore=4,
            spaceAfter=10,
        ))

        self.styles.add(ParagraphStyle(
            name="Body",
            parent=self.styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=self.COLORS["ink_soft"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="BodyMuted",
            parent=self.styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=self.COLORS["muted"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="MetricValue",
            parent=self.styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=self.COLORS["ink"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="MetricLabel",
            parent=self.styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10,
            textColor=self.COLORS["muted"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="TableHeading",
            parent=self.styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=white,
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="KeyLabel",
            parent=self.styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10,
            textColor=self.COLORS["muted"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="KeyValue",
            parent=self.styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=self.COLORS["ink_soft"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="ItemTitle",
            parent=self.styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=14,
            textColor=self.COLORS["ink"],
            alignment=TA_LEFT,
        ))

        self.styles.add(ParagraphStyle(
            name="StatusText",
            parent=self.styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=9,
            textColor=white,
            alignment=TA_CENTER,
        ))

    def _safe_text(self, value: Any, default: str = "N/A") -> str:
        if value is None:
            return default
        if isinstance(value, str):
            cleaned = " ".join(value.split())
            return cleaned or default
        return str(value)

    def _format_display_date(self, value: Any, default: str = "N/A") -> str:
        parsed = _parse_date(value)
        return parsed.strftime("%d %b %Y") if parsed else self._safe_text(value, default)

    def _format_display_datetime(self, value: Any, default: str = "N/A") -> str:
        parsed = _parse_datetime(value)
        return parsed.strftime("%d %b %Y %H:%M") if parsed else self._safe_text(value, default)

    def _format_time_only(self, value: Any, default: str = "N/A") -> str:
        parsed = _parse_datetime(value)
        return parsed.strftime("%H:%M") if parsed else self._safe_text(value, default)

    def _format_duration(self, seconds: Any) -> str:
        try:
            total_seconds = int(seconds or 0)
        except (TypeError, ValueError):
            return "Not recorded"

        if total_seconds <= 0:
            return "Not recorded"

        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)

        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if secs and not hours:
            parts.append(f"{secs}s")
        return " ".join(parts) or "0s"

    def _format_shift_label(self, shift: Any) -> str:
        normalized = str(shift or "").strip().upper()
        labels = {
            "MORNING": "Morning Shift",
            "AFTERNOON": "Afternoon Shift",
            "NIGHT": "Night Shift",
        }
        if normalized in labels:
            return labels[normalized]
        return self._safe_text(shift, "Checklist Shift")

    def _escape_paragraph_text(self, value: Any, default: str = "N/A") -> str:
        return escape(self._safe_text(value, default)).replace("\n", "<br/>")

    def _status_color(self, status: Any) -> HexColor:
        normalized = str(status or "").upper()
        return {
            "COMPLETED": self.COLORS["green"],
            "COMPLETED_WITH_EXCEPTIONS": self.COLORS["warning"],
            "IN_PROGRESS": self.COLORS["blue"],
            "PENDING_REVIEW": self.COLORS["warning"],
            "PENDING": self.COLORS["muted"],
            "OPEN": self.COLORS["muted_soft"],
            "SKIPPED": self.COLORS["warning"],
            "FAILED": self.COLORS["danger"],
            "INCOMPLETE": self.COLORS["danger"],
        }.get(normalized, self.COLORS["muted"])

    def _status_fill_color(self, status: Any) -> HexColor:
        normalized = str(status or "").upper()
        return {
            "COMPLETED": self.COLORS["green_soft"],
            "COMPLETED_WITH_EXCEPTIONS": self.COLORS["warning_soft"],
            "IN_PROGRESS": self.COLORS["blue_soft"],
            "PENDING_REVIEW": self.COLORS["warning_soft"],
            "PENDING": self.COLORS["surface_tint"],
            "OPEN": self.COLORS["surface_tint"],
            "SKIPPED": self.COLORS["warning_soft"],
            "FAILED": self.COLORS["danger_soft"],
            "INCOMPLETE": self.COLORS["danger_soft"],
        }.get(normalized, self.COLORS["surface_tint"])

    def _status_label(self, status: Any) -> str:
        return self._safe_text(str(status or "").replace("_", " ").title(), "Unknown")

    def _color_hex(self, color: HexColor) -> str:
        return "#%02x%02x%02x" % (
            int(color.red * 255),
            int(color.green * 255),
            int(color.blue * 255),
        )

    def _create_status_badge(self, status: Any, width: float = 1.28 * inch) -> Table:
        label = self._status_label(status).upper()
        badge = Table(
            [[Paragraph(escape(label), self.styles["StatusText"])]],
            colWidths=[width],
        )
        badge.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self._status_color(status)),
            ("BOX", (0, 0), (-1, -1), 0, self._status_color(status)),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        return badge

    def _load_logo(self, max_width: float, max_height: float) -> Any:
        candidate_paths = [
            os.path.join(os.path.dirname(__file__), "..", "static", "logo.png"),
            os.path.join(os.path.dirname(__file__), "pdf_assets", "logo.png"),
        ]

        for path in candidate_paths:
            if os.path.exists(path):
                try:
                    image = Image(path)
                    image._restrictSize(max_width, max_height)
                    return image
                except Exception as exc:
                    logger.warning("Could not load logo for PDF: %s", exc)
        return Spacer(1, max_height)

    def _collect_completed_by_users(self, data: Dict[str, Any]) -> List[str]:
        users = []
        seen = set()

        for entry in data.get("completed_by_users") or []:
            name = self._safe_text(entry.get("name"), "")
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            users.append(name)

        if users:
            return users

        for item in data.get("items_data") or []:
            name = self._safe_text(item.get("completed_by_name"), "")
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            users.append(name)

        return users

    def _build_summary_table(self, title: str, rows: List[List[Any]]) -> Table:
        table_data: List[List[Any]] = [[Paragraph(escape(title), self.styles["TableHeading"]), ""]]

        for label, value in rows:
            value_flowable = value if hasattr(value, "wrap") else Paragraph(
                self._escape_paragraph_text(value),
                self.styles["KeyValue"],
            )
            table_data.append([
                Paragraph(escape(label), self.styles["KeyLabel"]),
                value_flowable,
            ])

        table = Table(table_data, colWidths=[145, self.content_width - 145])
        table.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), self.COLORS["ink"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("BACKGROUND", (0, 1), (0, -1), self.COLORS["surface_tint"]),
            ("BACKGROUND", (1, 1), (1, -1), self.COLORS["surface"]),
            ("GRID", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return table

    def _build_metric_card(self, label: str, value: str, accent: HexColor) -> Table:
        card_contents = [
            Paragraph(escape(label.upper()), self.styles["MetricLabel"]),
            Spacer(1, 4),
            Paragraph(escape(value), self.styles["MetricValue"]),
        ]
        card = Table(
            [[card_contents]],
            colWidths=[self.content_width / 2 - 10],
        )
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self.COLORS["surface"]),
            ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBEFORE", (0, 0), (0, 0), 4, accent),
        ]))
        return card

    def _build_callout_panel(self, title: str, text: str) -> Table:
        panel_contents = [
            Paragraph(escape(title), self.styles["KeyLabel"]),
            Spacer(1, 4),
            Paragraph(self._escape_paragraph_text(text, "No additional operational notes."), self.styles["Body"]),
        ]
        panel = Table(
            [[panel_contents]],
            colWidths=[self.content_width],
        )
        panel.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self.COLORS["surface_alt"]),
            ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return panel

    def _build_info_grid(self, pairs: List[List[str]]) -> Table:
        cells = []
        for label, value in pairs:
            cell = Paragraph(
                f"<b>{escape(label)}</b><br/>{self._escape_paragraph_text(value)}",
                self.styles["Body"],
            )
            cells.append(cell)

        if len(cells) % 2:
            cells.append(Paragraph("", self.styles["Body"]))

        rows = [cells[index:index + 2] for index in range(0, len(cells), 2)]
        grid = Table(rows, colWidths=[self.content_width / 2, self.content_width / 2])
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self.COLORS["surface"]),
            ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return grid

    def generate_checklist_pdf(self, instance_data: Dict[str, Any]) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=self.margin_right,
            leftMargin=self.margin_left,
            topMargin=self.margin_top,
            bottomMargin=self.margin_bottom,
        )

        story: List[Any] = []
        story.extend(self._create_cover_page(instance_data))
        story.extend(self._create_operational_snapshot(instance_data))
        story.append(PageBreak())
        story.extend(self._create_detailed_items(instance_data))

        handover_section = self._create_handover_notes_section(instance_data)
        if handover_section:
            story.append(PageBreak())
            story.extend(handover_section)

        doc.build(
            story,
            onFirstPage=lambda canvas_obj, doc_obj: self._draw_page_chrome(canvas_obj, doc_obj, instance_data),
            onLaterPages=lambda canvas_obj, doc_obj: self._draw_page_chrome(canvas_obj, doc_obj, instance_data),
        )

        buffer.seek(0)
        return buffer.getvalue()

    def _draw_page_chrome(self, canvas_obj, doc, data: Dict[str, Any]):
        canvas_obj.saveState()
        page_width, page_height = doc.pagesize

        canvas_obj.setFillColor(self.COLORS["hero"])
        canvas_obj.rect(0, page_height - 20, page_width, 20, fill=True, stroke=False)
        canvas_obj.setFillColor(self.COLORS["sky"])
        canvas_obj.rect(0, page_height - 20, page_width, 4, fill=True, stroke=False)

        canvas_obj.setFillColor(white)
        canvas_obj.setFont("Helvetica-Bold", 9)
        canvas_obj.drawString(doc.leftMargin, page_height - 14, "SentinelOps Checklist Report")

        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.drawRightString(
            page_width - doc.rightMargin,
            page_height - 14,
            f"{self._format_shift_label(data.get('shift'))} | {self._format_display_date(data.get('checklist_date'))}",
        )

        canvas_obj.setStrokeColor(self.COLORS["border"])
        canvas_obj.line(doc.leftMargin, doc.bottomMargin - 8, page_width - doc.rightMargin, doc.bottomMargin - 8)

        canvas_obj.setFillColor(self.COLORS["muted"])
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.drawString(doc.leftMargin, doc.bottomMargin - 20, "SentinelOps Internal Use")
        canvas_obj.drawRightString(page_width - doc.rightMargin, doc.bottomMargin - 20, f"Page {canvas_obj.getPageNumber()}")
        canvas_obj.restoreState()

    def _create_cover_page(self, data: Dict[str, Any]) -> List[Any]:
        elements: List[Any] = []
        hero_right_width = self.content_width * 0.33
        hero_left_width = self.content_width - hero_right_width

        logo = self._load_logo(max_width=2.1 * inch, max_height=0.9 * inch)
        template_name = self._safe_text(data.get("template_name"), "Operational Checklist")
        template_description = self._safe_text(data.get("template_description"), "")
        shift_label = self._format_shift_label(data.get("shift"))
        date_label = self._format_display_date(data.get("checklist_date"))

        left_column = [
            logo,
            Spacer(1, 10),
            Paragraph("CHECKLIST OPERATIONAL RECORD", self.styles["ReportEyebrow"]),
            Paragraph(escape(template_name), self.styles["HeroTitle"]),
            Paragraph(
                self._escape_paragraph_text(
                    template_description or "Detailed shift execution record generated from the SentinelOps operational checklist."
                ),
                self.styles["HeroSubtitle"],
            ),
            Spacer(1, 10),
            Paragraph(
                f"<font color='#2563eb'><b>{escape(shift_label)}</b></font><br/><font color='#475569'>{escape(date_label)}</font>",
                self.styles["HeroSubtitle"],
            ),
        ]

        reviewed_by = self._safe_text(data.get("closed_by_name"), "Pending review")
        review_stamp = self._format_display_datetime(data.get("closed_at"), "Awaiting closeout")
        status_panel_contents = [
            Paragraph("CHECKLIST STATUS", self.styles["ReportEyebrow"]),
            self._create_status_badge(data.get("instance_status"), width=1.3 * inch),
            Spacer(1, 6),
            Paragraph(
                f"<font color='#64748b'>Reviewed by</font><br/><font color='#0f172a'><b>{escape(reviewed_by)}</b></font>",
                self.styles["HeroSubtitle"],
            ),
            Spacer(1, 4),
            Paragraph(
                f"<font color='#64748b'>Review time</font><br/><font color='#0f172a'>{escape(review_stamp)}</font>",
                self.styles["HeroSubtitle"],
            ),
        ]
        status_panel = Table(
            [[status_panel_contents]],
            colWidths=[hero_right_width - 14],
        )
        status_panel.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self.COLORS["surface"]),
            ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))

        hero = Table(
            [[left_column, status_panel]],
            colWidths=[hero_left_width, hero_right_width],
        )
        hero.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self.COLORS["surface_alt"]),
            ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (0, 0), 18),
            ("RIGHTPADDING", (0, 0), (0, 0), 18),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
            ("RIGHTPADDING", (1, 0), (1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 18),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(hero)
        elements.append(Spacer(1, 16))

        completed_by_users = self._collect_completed_by_users(data)
        completed_by_value = (
            Paragraph("<br/>".join(escape(name) for name in completed_by_users), self.styles["KeyValue"])
            if completed_by_users
            else Paragraph("No completed checklist items recorded", self.styles["KeyValue"])
        )

        shift_window = " - ".join([
            self._format_time_only(data.get("shift_start"), ""),
            self._format_time_only(data.get("shift_end"), ""),
        ]).strip(" -") or "N/A"

        summary_rows = [
            ["Checklist Name", template_name],
            ["Operational Date", date_label],
            ["Shift", shift_label],
            ["Shift Window", shift_window],
            ["Section", self._safe_text(data.get("section_name"), "Unassigned")],
            ["Created By", self._safe_text(data.get("created_by_name"), "System")],
            ["Completed By", completed_by_value],
            ["Reviewed By", reviewed_by],
            ["Reviewed At", review_stamp],
        ]

        elements.append(self._build_summary_table("Checklist Summary", summary_rows))
        elements.append(Spacer(1, 14))
        return elements

    def _create_operational_snapshot(self, data: Dict[str, Any]) -> List[Any]:
        elements: List[Any] = [Paragraph("Operational Snapshot", self.styles["SectionTitle"])]

        summary = data.get("summary_statistics") or {}
        total_items = int(summary.get("total_items", 0) or 0)
        completed_items = int(summary.get("completed_items", 0) or 0)
        skipped_items = int(summary.get("skipped_items", 0) or 0)
        failed_items = int(summary.get("failed_items", 0) or 0)
        open_items = max(total_items - completed_items - skipped_items - failed_items, 0)
        exception_count = int(data.get("exception_count", skipped_items + failed_items) or 0)
        completion_rate = (completed_items / total_items * 100) if total_items else 0

        cards = [
            self._build_metric_card("Completion rate", f"{completion_rate:.0f}%", self.COLORS["blue"]),
            self._build_metric_card("Completed items", f"{completed_items} / {total_items}", self.COLORS["green"]),
            self._build_metric_card("Exceptions", str(exception_count), self.COLORS["warning"] if exception_count else self.COLORS["green"]),
            self._build_metric_card("Execution time", self._format_duration(data.get("completion_time_seconds")), self.COLORS["sky"]),
        ]
        metrics_grid = Table(
            [[cards[0], cards[1]], [cards[2], cards[3]]],
            colWidths=[self.content_width / 2 - 6, self.content_width / 2 - 6],
            hAlign="LEFT",
        )
        metrics_grid.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(metrics_grid)
        elements.append(Spacer(1, 8))

        narrative = (
            f"{self._format_shift_label(data.get('shift'))} recorded {completed_items} completed item(s) "
            f"out of {total_items}. Open items remaining at review time: {open_items}. "
            f"Exceptions captured: {exception_count}. "
            f"Review outcome: {self._status_label(data.get('instance_status'))}."
        )
        elements.append(self._build_callout_panel("Operational narrative", narrative))
        elements.append(Spacer(1, 10))
        return elements

    def _create_item_section(self, item: Dict[str, Any]) -> List[Any]:
        elements: List[Any] = []
        item_title = self._safe_text(item.get("title"), "Untitled Checklist Item")
        item_status = item.get("status")

        header = Table(
            [[
                Paragraph(escape(item_title), self.styles["ItemTitle"]),
                self._create_status_badge(item_status),
            ]],
            colWidths=[self.content_width - 1.45 * inch, 1.45 * inch],
        )
        header.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self._status_fill_color(item_status)),
            ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(header)
        elements.append(Spacer(1, 6))

        item_pairs = [
            ["Type", self._safe_text(item.get("item_type"), "Routine")],
            ["Severity", self._safe_text(item.get("severity"), "N/A")],
            ["Required", "Yes" if item.get("is_required") else "No"],
            ["Completed By", self._safe_text(item.get("completed_by_name"), "Awaiting completion")],
            ["Scheduled", self._safe_text(item.get("scheduled_time"), "Not scheduled")],
        ]
        elements.append(self._build_info_grid(item_pairs))

        description = self._safe_text(item.get("description"), "")
        if description:
            elements.append(Spacer(1, 6))
            elements.append(self._build_callout_panel("Item description", description))

        if item.get("skipped_reason"):
            elements.append(Spacer(1, 6))
            elements.append(self._build_callout_panel("Skip reason", self._safe_text(item.get("skipped_reason"))))
        if item.get("failure_reason"):
            elements.append(Spacer(1, 6))
            elements.append(self._build_callout_panel("Failure reason", self._safe_text(item.get("failure_reason"))))

        subitems = item.get("subitems") or []
        if subitems:
            elements.append(Spacer(1, 8))
            elements.append(Paragraph("Subitems", self.styles["KeyLabel"]))
            elements.append(self._create_subitems_table(subitems))

        elements.append(Spacer(1, 14))
        return elements

    def _create_subitems_table(self, subitems: List[Dict[str, Any]]) -> Table:
        table_data: List[List[Any]] = [[
            Paragraph("Status", self.styles["TableHeading"]),
            Paragraph("Subitem", self.styles["TableHeading"]),
            Paragraph("Completed By", self.styles["TableHeading"]),
            Paragraph("Notes", self.styles["TableHeading"]),
        ]]

        for subitem in subitems:
            status = self._status_label(subitem.get("status"))
            note_parts = []
            description = self._safe_text(subitem.get("description"), "")
            if description:
                note_parts.append(description)
            completed_at = self._format_display_datetime(subitem.get("completed_at"), "")
            if completed_at and completed_at != "N/A":
                note_parts.append(f"Completed: {completed_at}")
            if subitem.get("skipped_reason"):
                note_parts.append(f"Skip: {self._safe_text(subitem.get('skipped_reason'))}")
            if subitem.get("failure_reason"):
                note_parts.append(f"Failure: {self._safe_text(subitem.get('failure_reason'))}")

            title = self._safe_text(subitem.get("title"), "Untitled subitem")
            table_data.append([
                Paragraph(
                    f"<font color='{self._color_hex(self._status_color(subitem.get('status')))}'><b>{escape(status)}</b></font>",
                    self.styles["Body"],
                ),
                Paragraph(
                    f"<b>{escape(title)}</b><br/><font color='#64748b'>{escape(self._safe_text(subitem.get('item_type'), 'Routine'))}</font>",
                    self.styles["Body"],
                ),
                Paragraph(self._escape_paragraph_text(subitem.get("completed_by_name"), "Not recorded"), self.styles["Body"]),
                Paragraph(self._escape_paragraph_text(" | ".join(note_parts), "No notes recorded"), self.styles["BodyMuted"]),
            ])

        table = Table(
            table_data,
            colWidths=[64, 185, 100, self.content_width - 64 - 185 - 100],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.COLORS["ink"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("BACKGROUND", (0, 1), (-1, -1), self.COLORS["surface"]),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [self.COLORS["surface"], self.COLORS["surface_alt"]]),
            ("GRID", (0, 0), (-1, -1), 0.6, self.COLORS["border_soft"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return table

    def _create_detailed_items(self, data: Dict[str, Any]) -> List[Any]:
        elements: List[Any] = [Paragraph("Detailed Checklist Items", self.styles["SectionTitle"])]
        items_data = data.get("items_data") or []

        if not items_data:
            elements.append(Paragraph("No checklist items were found for this instance.", self.styles["Body"]))
            return elements

        for item in items_data:
            elements.extend(self._create_item_section(item))

        return elements

    def _create_handover_notes_section(self, data: Dict[str, Any]) -> List[Any]:
        handover_notes = data.get("handover_notes") or []

        if not handover_notes:
            return []

        elements: List[Any] = [Paragraph("Handover Notes", self.styles["SectionTitle"])]

        for note in handover_notes:
            priority = self._safe_text(note.get("priority"), "1")
            created_by = self._safe_text(note.get("created_by"), "Unknown operator")
            created_at = self._format_display_datetime(note.get("created_at"), "Time not recorded")
            content = self._safe_text(note.get("note"), "No content provided")

            header = Table(
                [[
                    Paragraph(f"<b>Priority {escape(priority)}</b>", self.styles["Body"]),
                    Paragraph(f"<b>{escape(created_by)}</b><br/>{escape(created_at)}", self.styles["BodyMuted"]),
                ]],
                colWidths=[self.content_width * 0.28, self.content_width * 0.72],
            )
            header.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), self.COLORS["surface_tint"]),
                ("BOX", (0, 0), (-1, -1), 0.8, self.COLORS["border_soft"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(header)
            elements.append(Spacer(1, 4))
            elements.append(self._build_callout_panel("Note", content))
            elements.append(Spacer(1, 10))

        return elements


def generate_checklist_pdf(instance_data: Dict[str, Any]) -> bytes:
    try:
        generator = SentinelOpsPDFGenerator()
        return generator.generate_checklist_pdf(instance_data)
    except Exception as exc:
        logger.error("Error generating PDF: %s", exc)
        raise
