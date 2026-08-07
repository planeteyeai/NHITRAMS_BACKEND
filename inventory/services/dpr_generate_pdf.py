"""Render Asset Change Detection and Monitoring DPR PDF from generated JSON."""

from __future__ import annotations

import logging
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pdf.utils import download_image

logger = logging.getLogger(__name__)

MARGIN = 20 * mm
HEADER_BG = colors.HexColor('#1F4E79')
HEADER_FG = colors.white
ALT_ROW = colors.HexColor('#F2F2F2')
GRID = colors.HexColor('#666666')
TITLE_COLOR = colors.HexColor('#1F4E79')


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else '')), style)


def _cell(text, style) -> Paragraph:
    value = text if text is not None else ''
    if isinstance(value, float):
        if value == int(value):
            value = str(int(value))
        else:
            value = f'{value:.3f}'.rstrip('0').rstrip('.')
    return Paragraph(escape(str(value)), style)


def _safe_filename_part(name: str) -> str:
    cleaned = re.sub(r'[^\w\-]+', '_', str(name or '').strip())
    cleaned = cleaned.strip('_') or 'Project'
    return cleaned[:80]


def build_dpr_pdf_filename(project_name: str, from_date, to_date) -> str:
    """Build DPR_<Project>_<YYYYMMDD>_<YYYYMMDD>.pdf"""

    def _ymd(value) -> str:
        text = str(value or '').strip()
        digits = text.replace('-', '').replace('/', '')[:8]
        if len(digits) == 8 and digits.isdigit():
            return digits
        if hasattr(value, 'strftime'):
            return value.strftime('%Y%m%d')
        from datetime import datetime

        return datetime.now().strftime('%Y%m%d')

    return (
        f'DPR_{_safe_filename_part(project_name)}_'
        f'{_ymd(from_date)}_{_ymd(to_date)}.pdf'
    )


class AssetChangeDPRPDFBuilder:
    """Build a print-ready DPR PDF from DPRGenerationService JSON payload."""

    def __init__(self, payload: dict, *, temp_dir: str):
        self.payload = payload or {}
        self.temp_dir = temp_dir
        self.styles = self._build_styles()

    def _build_styles(self) -> dict:
        base = getSampleStyleSheet()
        return {
            'title': ParagraphStyle(
                'DPRTitle',
                parent=base['Heading1'],
                fontName='Helvetica-Bold',
                fontSize=16,
                leading=20,
                alignment=TA_CENTER,
                textColor=TITLE_COLOR,
                spaceAfter=2 * mm,
            ),
            'subtitle': ParagraphStyle(
                'DPRSubtitle',
                parent=base['Normal'],
                fontName='Helvetica-Bold',
                fontSize=11,
                leading=14,
                alignment=TA_CENTER,
                textColor=colors.HexColor('#333333'),
                spaceAfter=6 * mm,
            ),
            'section': ParagraphStyle(
                'DPRSection',
                parent=base['Heading2'],
                fontName='Helvetica-Bold',
                fontSize=12,
                leading=15,
                textColor=TITLE_COLOR,
                spaceBefore=4 * mm,
                spaceAfter=3 * mm,
            ),
            'body': ParagraphStyle(
                'DPRBody',
                parent=base['Normal'],
                fontName='Helvetica',
                fontSize=9,
                leading=11,
                alignment=TA_LEFT,
            ),
            'meta_label': ParagraphStyle(
                'DPRMetaLabel',
                parent=base['Normal'],
                fontName='Helvetica-Bold',
                fontSize=9,
                leading=11,
            ),
            'meta_value': ParagraphStyle(
                'DPRMetaValue',
                parent=base['Normal'],
                fontName='Helvetica',
                fontSize=9,
                leading=11,
            ),
            'th': ParagraphStyle(
                'DPRTH',
                parent=base['Normal'],
                fontName='Helvetica-Bold',
                fontSize=7,
                leading=9,
                textColor=HEADER_FG,
                alignment=TA_CENTER,
            ),
            'td': ParagraphStyle(
                'DPRTD',
                parent=base['Normal'],
                fontName='Helvetica',
                fontSize=7,
                leading=9,
                alignment=TA_LEFT,
            ),
            'td_center': ParagraphStyle(
                'DPRTDCenter',
                parent=base['Normal'],
                fontName='Helvetica',
                fontSize=7,
                leading=9,
                alignment=TA_CENTER,
            ),
            'empty': ParagraphStyle(
                'DPREmpty',
                parent=base['Normal'],
                fontName='Helvetica-Oblique',
                fontSize=9,
                leading=12,
                textColor=colors.HexColor('#666666'),
                alignment=TA_CENTER,
                spaceBefore=2 * mm,
                spaceAfter=2 * mm,
            ),
            'photo_caption': ParagraphStyle(
                'DPRPhotoCaption',
                parent=base['Normal'],
                fontName='Helvetica',
                fontSize=8,
                leading=10,
                alignment=TA_CENTER,
            ),
            'footer': ParagraphStyle(
                'DPRFooter',
                parent=base['Normal'],
                fontName='Helvetica',
                fontSize=8,
                textColor=colors.HexColor('#555555'),
                alignment=TA_CENTER,
            ),
        }

    def build(self, output_path: str) -> str:
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN + 8 * mm,
            title='Asset Change Detection and Monitoring DPR',
            author='NHIT RAMS',
        )
        story = []
        story.extend(self._title_block())
        story.extend(self._project_details_section())
        story.extend(self._overall_summary_section())
        story.extend(self._removed_assets_section())
        story.extend(self._photo_section(
            title='3.1 Removed/Damaged Assets Photos',
            photos=self.payload.get('removed_damaged_asset_photos') or [],
            empty_message='No Inventory Photos Found',
            type_key='asset_type',
        ))
        story.extend(self._routine_maintenance_section())
        story.extend(self._photo_section(
            title='4.1 Routine Maintenance Photos',
            photos=self.payload.get('routine_maintenance_photos') or [],
            empty_message='No Routine Maintenance Photos Found',
            type_key='maintenance_type',
        ))

        details = self.payload.get('project_details') or {}
        project_name = details.get('project_name') or ''
        generated = (
            details.get('generation_datetime_display')
            or details.get('generation_date_display')
            or ''
        )

        def _footer(canvas, doc_):
            canvas.saveState()
            canvas.setStrokeColor(GRID)
            canvas.setLineWidth(0.4)
            y = 12 * mm
            canvas.line(MARGIN, y + 6 * mm, A4[0] - MARGIN, y + 6 * mm)
            canvas.setFont('Helvetica', 8)
            canvas.setFillColor(colors.HexColor('#555555'))
            canvas.drawString(MARGIN, y, 'NHIT • Asset Change Detection and Monitoring DPR')
            canvas.drawRightString(
                A4[0] - MARGIN,
                y,
                f'Page {doc_.page}  |  Generated: {generated}  |  {project_name[:40]}',
            )
            canvas.restoreState()

        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        return output_path

    def _title_block(self) -> list:
        return [
            Paragraph('DAILY PROGRESS REPORT (DPR)', self.styles['title']),
            Paragraph(
                'Asset Change Detection and Monitoring Report',
                self.styles['subtitle'],
            ),
        ]

    def _project_details_section(self) -> list:
        details = self.payload.get('project_details') or {}
        rows = [
            ['Parameter', 'Details'],
            ['Project Name', details.get('project_name') or ''],
            ['Chainage', details.get('chainage') or '-'],
            ['Total Project Length', details.get('total_project_length') or ''],
            ['Reporting Engineer Name', details.get('reporting_engineer_name') or ''],
            [
                'Assessment Date',
                details.get('assessment_date_display')
                or details.get('assessment_date')
                or '',
            ],
            [
                'Report Period',
                details.get('report_period_display')
                or (
                    f"{details.get('from_date_display') or details.get('from_date') or ''}"
                    f" - "
                    f"{details.get('to_date_display') or details.get('to_date') or ''}"
                ).strip(' -'),
            ],
            [
                'Report Generated',
                details.get('generation_datetime_display')
                or details.get('generation_date_display')
                or '',
            ],
        ]
        data = [
            [_cell(rows[0][0], self.styles['th']), _cell(rows[0][1], self.styles['th'])]
        ]
        for label, value in rows[1:]:
            data.append([
                _cell(label, self.styles['meta_label']),
                _cell(value, self.styles['meta_value']),
            ])

        table = Table(data, colWidths=[70 * mm, 100 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
            ('TEXTCOLOR', (0, 0), (-1, 0), HEADER_FG),
            ('GRID', (0, 0), (-1, -1), 0.4, GRID),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#E8EEF5')),
        ]))
        return [
            Paragraph('1. PROJECT DETAILS', self.styles['section']),
            table,
            Spacer(1, 3 * mm),
        ]

    def _styled_data_table(self, headers: list[str], rows: list[list], col_widths: list[float]) -> Table:
        data = [[_cell(h, self.styles['th']) for h in headers]]
        for row in rows:
            data.append([
                _cell(value, self.styles['td_center'] if idx == 0 else self.styles['td'])
                for idx, value in enumerate(row)
            ])
        table = Table(data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
            ('TEXTCOLOR', (0, 0), (-1, 0), HEADER_FG),
            ('GRID', (0, 0), (-1, -1), 0.3, GRID),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]
        for i in range(1, len(data)):
            if i % 2 == 0:
                style_cmds.append(('BACKGROUND', (0, i), (-1, i), ALT_ROW))
        table.setStyle(TableStyle(style_cmds))
        return table

    def _overall_summary_section(self) -> list:
        elements = [Paragraph('2. Overall Summary', self.styles['section'])]
        summary = self.payload.get('overall_summary') or []
        if not summary:
            elements.append(Paragraph('No Inventory Records Found', self.styles['empty']))
            return elements

        headers = ['Asset Type', 'Total Quantity', 'Damaged Quantity', 'Available Quantity']
        rows = [
            [
                row.get('asset_type', ''),
                row.get('total_quantity', 0),
                row.get('damaged_quantity', 0),
                row.get('available_quantity', 0),
            ]
            for row in summary
        ]
        widths = [70 * mm, 35 * mm, 35 * mm, 35 * mm]
        elements.append(self._styled_data_table(headers, rows, widths))
        elements.append(Spacer(1, 3 * mm))
        return elements

    def _removed_assets_section(self) -> list:
        elements = [Paragraph('3. Details of Removed/Damaged Assets', self.styles['section'])]
        assets = self.payload.get('removed_damaged_assets') or []
        if not assets:
            elements.append(Paragraph('No Inventory Records Found', self.styles['empty']))
            return elements

        headers = [
            'Sr. No.', 'Asset Type', 'Chainage', 'Latitude', 'Longitude', 'Side',
            'Date and Time', 'Description', 'Evidence Available', 'Nos',
            'Length (m)', 'Width (m)', 'Depth (m)', 'Total Quantity', 'Unit', 'Severity',
        ]
        rows = []
        for row in assets:
            rows.append([
                row.get('sr_no', ''),
                row.get('asset_type', ''),
                row.get('chainage', ''),
                row.get('latitude', ''),
                row.get('longitude', ''),
                row.get('side', ''),
                row.get('date_and_time', ''),
                row.get('description', ''),
                row.get('evidence_available', ''),
                row.get('nos', ''),
                row.get('length', ''),
                row.get('width', ''),
                row.get('depth', ''),
                row.get('total_quantity', ''),
                row.get('unit', ''),
                row.get('severity', ''),
            ])

        # Fit many columns on A4 portrait usable width (~170mm).
        usable = A4[0] - 2 * MARGIN
        weights = [0.045, 0.10, 0.07, 0.06, 0.06, 0.05, 0.09, 0.09, 0.09, 0.04, 0.05, 0.05, 0.05, 0.06, 0.04, 0.05]
        widths = [usable * w for w in weights]
        elements.append(self._styled_data_table(headers, rows, widths))
        elements.append(Spacer(1, 3 * mm))
        return elements

    def _routine_maintenance_section(self) -> list:
        elements = [Paragraph('4. Details of Routine Maintenance', self.styles['section'])]
        records = self.payload.get('routine_maintenance') or []
        if not records:
            elements.append(
                Paragraph('No Routine Maintenance Records Found', self.styles['empty'])
            )
            return elements

        headers = [
            'Sr. No.', 'Maintenance Type', 'Chainage', 'Latitude', 'Longitude', 'Side',
            'Date and Time', 'Description', 'Evidence Available', 'Nos',
            'Length (m)', 'Width (m)', 'Depth (m)', 'Total Quantity', 'Unit', 'Severity',
        ]
        rows = []
        for row in records:
            rows.append([
                row.get('sr_no', ''),
                row.get('maintenance_type', ''),
                row.get('chainage', ''),
                row.get('latitude', ''),
                row.get('longitude', ''),
                row.get('side', ''),
                row.get('date_and_time', ''),
                row.get('description', ''),
                row.get('evidence_available', ''),
                row.get('nos', ''),
                row.get('length', ''),
                row.get('width', ''),
                row.get('depth', ''),
                row.get('total_quantity', ''),
                row.get('unit', ''),
                row.get('severity', ''),
            ])

        usable = A4[0] - 2 * MARGIN
        weights = [0.045, 0.12, 0.07, 0.06, 0.06, 0.05, 0.09, 0.08, 0.09, 0.04, 0.05, 0.05, 0.05, 0.06, 0.04, 0.05]
        widths = [usable * w for w in weights]
        elements.append(self._styled_data_table(headers, rows, widths))
        elements.append(Spacer(1, 3 * mm))
        return elements

    def _photo_card(self, photo: dict, type_key: str):
        image_no = photo.get('image_number') or photo.get('image_label') or ''
        asset = photo.get(type_key) or ''
        chainage = photo.get('chainage') or ''
        caption = Paragraph(
            f'Image {escape(str(image_no))}<br/>'
            f'{escape(str(asset))}<br/>'
            f'{escape(str(chainage))}',
            self.styles['photo_caption'],
        )

        local_path = download_image(photo.get('photo_url') or '', self.temp_dir)
        usable_width = (A4[0] - 2 * MARGIN - 8 * mm) / 2
        max_height = 55 * mm

        if local_path:
            try:
                img = Image(local_path)
                img.hAlign = 'CENTER'
                # Maintain aspect ratio within box.
                iw, ih = float(img.imageWidth), float(img.imageHeight)
                if iw <= 0 or ih <= 0:
                    raise ValueError('invalid image size')
                scale = min(usable_width / iw, max_height / ih)
                img.drawWidth = iw * scale
                img.drawHeight = ih * scale
                media = img
            except Exception:
                logger.warning('Failed to place image %s', photo.get('photo_url'), exc_info=True)
                media = Paragraph('Image unavailable', self.styles['empty'])
        else:
            media = Paragraph('Image unavailable', self.styles['empty'])

        card = Table([[caption], [media]], colWidths=[usable_width])
        card.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.4, GRID),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        return card

    def _photo_section(
        self,
        *,
        title: str,
        photos: list[dict],
        empty_message: str,
        type_key: str,
    ) -> list:
        elements = [Paragraph(title, self.styles['section'])]
        if not photos:
            elements.append(Paragraph(empty_message, self.styles['empty']))
            return elements

        usable_width = (A4[0] - 2 * MARGIN - 8 * mm) / 2
        for i in range(0, len(photos), 2):
            left = self._photo_card(photos[i], type_key)
            if i + 1 < len(photos):
                right = self._photo_card(photos[i + 1], type_key)
                row = Table([[left, right]], colWidths=[usable_width + 4 * mm, usable_width + 4 * mm])
            else:
                row = Table([[left, '']], colWidths=[usable_width + 4 * mm, usable_width + 4 * mm])
            row.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(KeepTogether([row, Spacer(1, 2 * mm)]))
        return elements


def render_asset_change_dpr_pdf(payload: dict, output_path: str, *, temp_dir: str) -> str:
    """Render PDF file from DPR JSON payload. Returns output_path.

    Does not query InventoryReport or RoutineMaintenanceReport — layout only.
    """
    builder = AssetChangeDPRPDFBuilder(payload, temp_dir=temp_dir)
    return builder.build(output_path)
