"""NHIT / Infra.AI PDF brand tokens matching the production report UI."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm

# ── Shared ───────────────────────────────────────────────────────────────────
WHITE = colors.white
BLACK = colors.HexColor('#111111')
PAGE_BG_LIGHT = colors.white
MARGIN = 14 * mm

# ── Distress theme (blue + yellow) ───────────────────────────────────────────
DISTRESS = {
    'cover_bg': colors.HexColor('#121212'),
    'banner': colors.HexColor('#1E6BFF'),
    'accent': colors.HexColor('#F5C518'),
    'card_bg': colors.HexColor('#1C1C1E'),
    'card_value': colors.HexColor('#3B82F6'),
    'card_label': colors.HexColor('#9CA3AF'),
    'table_header': colors.HexColor('#1E6BFF'),
    'table_row_bg': colors.HexColor('#1C1C1E'),
    'table_text': colors.white,
    'section_title': colors.white,
    'footer_text': colors.HexColor('#9CA3AF'),
    'detail_top_line': colors.HexColor('#1E6BFF'),
    'detail_header_bg': colors.HexColor('#1F2937'),
    'detail_circle': colors.HexColor('#2563EB'),
    'detail_badge': colors.HexColor('#DC2626'),
    'detail_badge_text': 'CRITICAL',
    'metric_bg': colors.HexColor('#1F2937'),
    'image_bg': colors.HexColor('#374151'),
    'footer_bar': colors.HexColor('#1F2937'),
    'url_text': colors.HexColor('#9CA3AF'),
    'table_alt': colors.HexColor('#F3F4F6'),
    'table_label': colors.HexColor('#6B7280'),
    'footer_name': 'NHIT • Road Distress Analysis Report',
}

# ── Inventory theme (green + gold) ───────────────────────────────────────────
INVENTORY = {
    'cover_bg': colors.HexColor('#121212'),
    'banner': colors.HexColor('#16A34A'),
    'accent': colors.HexColor('#EAB308'),
    'card_bg': colors.HexColor('#1C1C1E'),
    'card_value': colors.HexColor('#22C55E'),
    'card_label': colors.HexColor('#9CA3AF'),
    'table_header': colors.HexColor('#16A34A'),
    'table_row_bg': colors.HexColor('#1C1C1E'),
    'table_text': colors.white,
    'section_title': colors.white,
    'footer_text': colors.HexColor('#9CA3AF'),
    'detail_top_line': colors.HexColor('#16A34A'),
    'detail_header_bg': colors.HexColor('#1F2937'),
    'detail_circle': colors.HexColor('#22C55E'),
    'detail_badge': colors.HexColor('#16A34A'),
    'detail_badge_text': 'ASSET',
    'metric_bg': colors.HexColor('#1F2937'),
    'image_bg': colors.HexColor('#374151'),
    'footer_bar': colors.HexColor('#1F2937'),
    'url_text': colors.HexColor('#9CA3AF'),
    'table_alt': colors.HexColor('#F3F4F6'),
    'table_label': colors.HexColor('#6B7280'),
    'footer_name': 'NHIT • Road Inventory Asset Report',
}


def get_styles():
    base = getSampleStyleSheet()
    return {
        'cover_title': ParagraphStyle(
            'NHITCoverTitle', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=20, leading=24,
            textColor=WHITE, alignment=TA_LEFT, spaceAfter=2,
        ),
        'cover_subtitle': ParagraphStyle(
            'NHITCoverSubtitle', parent=base['Normal'],
            fontName='Helvetica', fontSize=10, leading=13,
            textColor=colors.HexColor('#F5C518'), alignment=TA_LEFT,
        ),
        'cover_section': ParagraphStyle(
            'NHITCoverSection', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=12, leading=15,
            textColor=WHITE, spaceBefore=4, spaceAfter=8,
        ),
        'card_value': ParagraphStyle(
            'NHITCardValue', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=22, leading=26,
            textColor=colors.HexColor('#3B82F6'), alignment=TA_CENTER,
        ),
        'card_label': ParagraphStyle(
            'NHITCardLabel', parent=base['Normal'],
            fontName='Helvetica', fontSize=8, leading=10,
            textColor=colors.HexColor('#9CA3AF'), alignment=TA_CENTER,
        ),
        'table_header': ParagraphStyle(
            'NHITTableHeader', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=9, leading=11,
            textColor=WHITE, alignment=TA_LEFT,
        ),
        'table_header_center': ParagraphStyle(
            'NHITTableHeaderCenter', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=9, leading=11,
            textColor=WHITE, alignment=TA_CENTER,
        ),
        'table_cell_dark': ParagraphStyle(
            'NHITTableCellDark', parent=base['Normal'],
            fontName='Helvetica', fontSize=9, leading=11,
            textColor=WHITE, alignment=TA_LEFT,
        ),
        'table_cell_dark_center': ParagraphStyle(
            'NHITTableCellDarkCenter', parent=base['Normal'],
            fontName='Helvetica', fontSize=9, leading=11,
            textColor=WHITE, alignment=TA_CENTER,
        ),
        'cover_meta': ParagraphStyle(
            'NHITCoverMeta', parent=base['Normal'],
            fontName='Helvetica', fontSize=8, leading=10,
            textColor=colors.HexColor('#9CA3AF'), alignment=TA_CENTER,
        ),
        'detail_title': ParagraphStyle(
            'NHITDetailTitle', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=16, leading=19,
            textColor=WHITE, alignment=TA_LEFT,
        ),
        'detail_ts': ParagraphStyle(
            'NHITDetailTs', parent=base['Normal'],
            fontName='Helvetica', fontSize=8, leading=10,
            textColor=colors.HexColor('#D1D5DB'), alignment=TA_LEFT,
        ),
        'badge': ParagraphStyle(
            'NHITBadge', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=8, leading=10,
            textColor=WHITE, alignment=TA_CENTER,
        ),
        'metric_label': ParagraphStyle(
            'NHITMetricLabel', parent=base['Normal'],
            fontName='Helvetica', fontSize=8, leading=10,
            textColor=colors.HexColor('#D1D5DB'), alignment=TA_LEFT,
        ),
        'metric_value': ParagraphStyle(
            'NHITMetricValue', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=8, leading=10,
            textColor=WHITE, alignment=TA_RIGHT,
        ),
        'placeholder': ParagraphStyle(
            'NHITPlaceholder', parent=base['Normal'],
            fontName='Helvetica', fontSize=11, leading=14,
            textColor=WHITE, alignment=TA_CENTER,
        ),
        'url': ParagraphStyle(
            'NHITUrl', parent=base['Normal'],
            fontName='Helvetica', fontSize=6.5, leading=8,
            textColor=colors.HexColor('#9CA3AF'), alignment=TA_LEFT,
        ),
        'meta_label': ParagraphStyle(
            'NHITMetaLabel', parent=base['Normal'],
            fontName='Helvetica-Bold', fontSize=9, leading=11,
            textColor=colors.HexColor('#6B7280'), alignment=TA_LEFT,
        ),
        'meta_value': ParagraphStyle(
            'NHITMetaValue', parent=base['Normal'],
            fontName='Helvetica', fontSize=9, leading=11,
            textColor=BLACK, alignment=TA_LEFT,
        ),
        'footer_light': ParagraphStyle(
            'NHITFooterLight', parent=base['Normal'],
            fontName='Helvetica', fontSize=7.5, leading=9,
            textColor=WHITE, alignment=TA_CENTER,
        ),
    }
