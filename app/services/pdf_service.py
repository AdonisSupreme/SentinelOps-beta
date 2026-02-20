"""
PDF Generation Service for SentinelOps Checklist Instances
Creates advanced, modern, and futuristic PDF reports
"""

import os
import io
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.colors import Color, HexColor, black, white, grey
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import logging

logger = logging.getLogger(__name__)

class SentinelOpsPDFGenerator:
    """Advanced PDF generator for SentinelOps checklist instances"""
    
    # SentinelOps Color Palette
    COLORS = {
        'primary': HexColor('#023aa3'),
        'secondary': HexColor('#7c0980'),
        'success': HexColor('#005423'),
        'warning': HexColor('#ffa502'),
        'error': HexColor('#ff4757'),
        'dark_bg': HexColor('#0a1428'),
        'light_bg': HexColor('#f8f9fa'),
        'text_primary': HexColor('#2c3e50'),
        'text_secondary': HexColor('#6c757d'),
        'border': HexColor('#dee2e6'),
        'accent': HexColor('#00d9ff')
    }
    
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Setup custom styles for SentinelOps branding"""
        # Title Style
        self.styles.add(ParagraphStyle(
            name='SentinelTitle',
            parent=self.styles['Title'],
            fontSize=28,
            spaceAfter=30,
            textColor=self.COLORS['primary'],
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))
        
        # Header Style
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading1'],
            fontSize=18,
            spaceAfter=12,
            spaceBefore=20,
            textColor=self.COLORS['primary'],
            borderWidth=0,
            borderColor=self.COLORS['primary'],
            borderPadding=5,
            fontName='Helvetica-Bold'
        ))
        
        # Subheader Style
        self.styles.add(ParagraphStyle(
            name='SubHeader',
            parent=self.styles['Heading2'],
            fontSize=14,
            spaceAfter=8,
            spaceBefore=15,
            textColor=self.COLORS['text_primary'],
            fontName='Helvetica-Bold'
        ))
        
        # Body Style
        self.styles.add(ParagraphStyle(
            name='SentinelBody',
            parent=self.styles['Normal'],
            fontSize=11,
            spaceAfter=6,
            textColor=self.COLORS['text_primary'],
            alignment=TA_JUSTIFY
        ))
        
        # Status Style
        self.styles.add(ParagraphStyle(
            name='StatusText',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=white,
            alignment=TA_CENTER
        ))
    
    def _create_header_footer(self, canvas_obj, doc):
        """Create custom header and footer"""
        canvas_obj.saveState()
        
        # Header
        canvas_obj.setFillColor(self.COLORS['primary'])
        canvas_obj.rect(0, doc.height + doc.topMargin, doc.width, 40, fill=True, stroke=False)
        
        canvas_obj.setFillColor(white)
        canvas_obj.setFont("Helvetica-Bold", 16)
        canvas_obj.drawString(doc.leftMargin, doc.height + doc.topMargin + 12, "SENTINEL OPS")
        
        canvas_obj.setFont("Helvetica", 10)
        canvas_obj.drawRightString(doc.width + doc.leftMargin, doc.height + doc.topMargin + 12, 
                                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Footer
        canvas_obj.setFillColor(self.COLORS['border'])
        canvas_obj.line(doc.leftMargin, doc.bottomMargin - 20, doc.width + doc.leftMargin, doc.bottomMargin - 20)
        
        canvas_obj.setFillColor(self.COLORS['text_secondary'])
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.drawString(doc.leftMargin, doc.bottomMargin - 30, 
                           f"Page {canvas_obj.getPageNumber()}")
        canvas_obj.drawRightString(doc.width + doc.leftMargin, doc.bottomMargin - 30, 
                                "Confidential - SentinelOps Internal Use")
        
        canvas_obj.restoreState()
    
    def _get_status_color(self, status: str) -> Color:
        """Get color based on status"""
        status_colors = {
            'COMPLETED': self.COLORS['success'],
            'IN_PROGRESS': self.COLORS['primary'],
            'PENDING': self.COLORS['warning'],
            'SKIPPED': self.COLORS['warning'],
            'FAILED': self.COLORS['error'],
            'OPEN': self.COLORS['text_secondary']
        }
        return status_colors.get(status.upper(), self.COLORS['text_secondary'])
    
    def _create_status_badge(self, status: str) -> Table:
        """Create a status badge table"""
        color = self._get_status_color(status)
        
        status_data = [[
            Paragraph(f'<font color="{self._color_to_hex(color)}" size="8"><b>{status.upper()}</b></font>', 
                     self.styles['Normal'])
        ]]
        
        status_table = Table(status_data, colWidths=[1.5*inch])
        status_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), color),
            ('BORDER', (0, 0), (-1, -1), 1, color),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        
        return status_table
    
    def _color_to_hex(self, color: Color) -> str:
        """Convert ReportLab Color to hex string"""
        if hasattr(color, 'hex'):
            return color.hex()
        return "#000000"
    
    def generate_checklist_pdf(self, instance_data: Dict[str, Any]) -> bytes:
        """Generate complete PDF for checklist instance"""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        story = []
        
        # Title Page
        story.extend(self._create_title_page(instance_data))
        story.append(PageBreak())
        
        # Executive Summary
        story.extend(self._create_executive_summary(instance_data))
        story.append(Spacer(1, 20))
        
        # Detailed Items
        story.extend(self._create_detailed_items(instance_data))
        
        # Build PDF
        doc.build(story, onFirstPage=self._create_header_footer, onLaterPages=self._create_header_footer)
        
        buffer.seek(0)
        return buffer.getvalue()
    
    def _create_title_page(self, data: Dict[str, Any]) -> List:
        """Create title page"""
        elements = []
        
        # Main Title
        elements.append(Paragraph("SENTINEL OPS", self.styles['SentinelTitle']))
        elements.append(Paragraph("CHECKLIST INSTANCE REPORT", self.styles['SentinelTitle']))
        elements.append(Spacer(1, 30))
        
        # Instance Information
        instance_info = Table([
            ['Checklist Name:', data.get('template_name', 'N/A')],
            ['Date:', data.get('checklist_date', 'N/A')],
            ['Shift:', data.get('shift', 'N/A')],
            ['Status:', self._create_status_badge(data.get('instance_status', 'N/A'))],
            ['Section:', data.get('section_name', 'N/A')],
            ['Created By:', data.get('created_by_name', 'N/A')],
        ], colWidths=[2*inch, 4*inch])
        
        instance_info.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, self.COLORS['border']),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 0), (0, -1), self.COLORS['light_bg']),
        ]))
        
        elements.append(instance_info)
        elements.append(Spacer(1, 40))
        
        # Summary Statistics
        summary = data.get('summary_statistics', {})
        stats_table = Table([
            ['Total Items:', str(summary.get('total_items', 0))],
            ['Completed Items:', str(summary.get('completed_items', 0))],
            ['Pending Items:', str(summary.get('pending_items', 0))],
            ['Total Subitems:', str(summary.get('total_subitems', 0))],
            ['Completed Subitems:', str(summary.get('completed_subitems', 0))],
            ['Completion Time:', f"{data.get('completion_time_seconds', 0)} seconds"],
        ], colWidths=[2*inch, 4*inch])
        
        stats_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, self.COLORS['border']),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 0), (0, -1), self.COLORS['light_bg']),
        ]))
        
        elements.append(stats_table)
        
        return elements
    
    def _create_executive_summary(self, data: Dict[str, Any]) -> List:
        """Create executive summary section"""
        elements = []
        
        elements.append(Paragraph("EXECUTIVE SUMMARY", self.styles['SectionHeader']))
        
        # Overview
        overview_text = f"""
        This report provides a comprehensive overview of the checklist instance "{data.get('template_name', 'N/A')}" 
        conducted on {data.get('checklist_date', 'N/A')} during the {data.get('shift', 'N/A')} shift. 
        The checklist covers {data.get('section_name', 'N/A')} section operations and was managed by 
        {data.get('created_by_name', 'N/A')}.
        """
        
        elements.append(Paragraph(overview_text, self.styles['SentinelBody']))
        elements.append(Spacer(1, 15))
        
        # Performance Metrics
        summary = data.get('summary_statistics', {})
        total_items = summary.get('total_items', 0)
        completed_items = summary.get('completed_items', 0)
        completion_rate = (completed_items / total_items * 100) if total_items > 0 else 0
        
        metrics_text = f"""
        <b>Performance Metrics:</b><br/>
        • Overall Completion Rate: {completion_rate:.1f}%<br/>
        • Items Completed: {completed_items} out of {total_items}<br/>
        • Subitems Completed: {summary.get('completed_subitems', 0)} out of {summary.get('total_subitems', 0)}<br/>
        • Exception Count: {data.get('exception_count', 0)}<br/>
        • Total Execution Time: {data.get('completion_time_seconds', 0)} seconds
        """
        
        elements.append(Paragraph(metrics_text, self.styles['SentinelBody']))
        
        return elements
    
    def _create_detailed_items(self, data: Dict[str, Any]) -> List:
        """Create detailed items section"""
        elements = []
        
        elements.append(Paragraph("DETAILED ITEMS BREAKDOWN", self.styles['SectionHeader']))
        
        items_data = data.get('items_data', [])
        if not items_data:
            elements.append(Paragraph("No items found for this checklist instance.", self.styles['SentinelBody']))
            return elements
        
        for item in items_data:
            elements.extend(self._create_item_section(item))
            elements.append(Spacer(1, 15))
        
        return elements
    
    def _create_item_section(self, item: Dict[str, Any]) -> List:
        """Create section for a single item"""
        elements = []
        
        # Item Header
        item_title = item.get('title', 'Untitled Item')
        elements.append(Paragraph(f"Item: {item_title}", self.styles['SubHeader']))
        
        # Item Details Table
        item_details = [
            ['Status:', self._create_status_badge(item.get('status', 'N/A'))],
            ['Required:', 'Yes' if item.get('is_required') else 'No'],
            ['Severity:', str(item.get('severity', 1))],
            ['Type:', item.get('item_type', 'N/A')],
        ]
        
        if item.get('completed_at'):
            item_details.append(['Completed At:', item.get('completed_at', 'N/A')])
        if item.get('completed_by_name'):
            item_details.append(['Completed By:', item.get('completed_by_name', 'N/A')])
        
        item_table = Table(item_details, colWidths=[1.5*inch, 4.5*inch])
        item_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, self.COLORS['border']),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (0, -1), self.COLORS['light_bg']),
        ]))
        
        elements.append(item_table)
        
        # Item Description
        description = item.get('description', '')
        if description:
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(f"<b>Description:</b> {description}", self.styles['SentinelBody']))
        
        # Subitems
        subitems = item.get('subitems', [])
        if subitems:
            elements.append(Spacer(1, 12))
            elements.append(Paragraph("Subitems:", self.styles['SubHeader']))
            
            for subitem in subitems:
                elements.extend(self._create_subitem_section(subitem))
        
        return elements
    
    def _create_subitem_section(self, subitem: Dict[str, Any]) -> List:
        """Create section for a single subitem"""
        elements = []
        
        # Subitem Title and Status
        title = subitem.get('title', 'Untitled Subitem')
        status = subitem.get('status', 'N/A')
        
        subitem_header = Table([
            [Paragraph(f"• {title}", self.styles['SentinelBody']), self._create_status_badge(status)]
        ], colWidths=[5*inch, 1.5*inch])
        
        subitem_header.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, self.COLORS['border']),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (0, 0), self.COLORS['light_bg']),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        
        elements.append(subitem_header)
        
        # Subitem Details
        description = subitem.get('description', '')
        if description:
            elements.append(Spacer(1, 6))
            elements.append(Paragraph(description, self.styles['SentinelBody']))
        
        # Reasons for non-completion
        if status in ['SKIPPED', 'FAILED']:
            reason_key = 'skipped_reason' if status == 'SKIPPED' else 'failure_reason'
            reason = subitem.get(reason_key, '')
            if reason:
                elements.append(Spacer(1, 6))
                reason_text = f"<b>Reason:</b> {reason}"
                elements.append(Paragraph(reason_text, self.styles['SentinelBody']))
        
        elements.append(Spacer(1, 8))
        return elements

def generate_checklist_pdf(instance_data: Dict[str, Any]) -> bytes:
    """Main function to generate checklist PDF"""
    try:
        generator = SentinelOpsPDFGenerator()
        return generator.generate_checklist_pdf(instance_data)
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        raise
