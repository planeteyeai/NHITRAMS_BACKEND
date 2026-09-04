"""ReportLab builders matching NHIT Infra.AI production PDF layouts exactly."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from . import styles as S
from .utils import count_by_field, download_image, prefetch_images, IMAGE_PREFETCH_WORKERS

logger = logging.getLogger(__name__)


def _fmt_date_short(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return f'{dt.month}/{dt.day}/{dt.year}'


def _fmt_datetime(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    # Match reference: 7/11/2026, 5:42:48 PM
    return f'{dt.month}/{dt.day}/{dt.year}, {dt.strftime("%I:%M:%S %p").lstrip("0")}'


def _fmt_record_date(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw:
        return '—'
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            d = datetime.strptime(raw[:10], fmt)
            return f'{d.day:02d}-{d.month:02d}-{d.year}'
        except ValueError:
            continue
    return raw


def _fmt_record_timestamp(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw:
        return _fmt_datetime()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            d = datetime.strptime(raw[:10], fmt)
            return f'{d.month}/{d.day}/{d.year}, 12:00:00 AM'
        except ValueError:
            continue
    return raw


def _depth_mm(value: Any) -> int:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if abs(v) <= 10:
        return int(round(v * 1000))
    return int(round(v))


def _area_m2(length: Any, width: Any) -> str:
    try:
        area = float(length or 0) * float(width or 0)
    except (TypeError, ValueError):
        area = 0.0
    text = f'{area:.4f}'.rstrip('0').rstrip('.')
    return f'{text} m²'


def _chainage(start: Any, end: Any) -> str:
    return f'{start} – {end} km'


class NumberedCanvas(pdfcanvas.Canvas):
    """Two-pass canvas to render 'Page X of Y' and page chrome."""

    def __init__(
        self,
        *args,
        footer_name: str = '',
        generated_text: str = '',
        theme: dict | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []
        self.footer_name = footer_name
        self.generated_text = generated_text
        self.theme = theme or S.DISTRESS

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(page_count)
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)

    def _draw_footer(self, page_count: int):
        page = self._pageNumber
        theme = self.theme
        width, _height = A4

        if page == 1:
            self.setFillColor(theme['footer_text'])
            self.setFont('Helvetica', 7.5)
            self.drawCentredString(width / 2, 18 * mm, self.generated_text)
            self.setStrokeColor(theme['footer_text'])
            self.setLineWidth(0.4)
            self.line(14 * mm, 14 * mm, width - 14 * mm, 14 * mm)
            self.setFont('Helvetica', 7)
            self.drawString(14 * mm, 8 * mm, self.footer_name)
            self.drawRightString(width - 14 * mm, 8 * mm, f'Page {page} of {page_count}')
        else:
            self.setFillColor(theme['footer_bar'])
            self.rect(0, 0, width, 12 * mm, fill=1, stroke=0)
            self.setFillColor(S.WHITE)
            self.setFont('Helvetica', 7.5)
            self.drawCentredString(width / 2, 4.5 * mm, self.generated_text)


class BannerHeader(Flowable):
    def __init__(self, title: str, theme: dict, width: float):
        super().__init__()
        self.title = title
        self.theme = theme
        self.width = width
        self.height = 28 * mm

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.theme['banner'])
        c.roundRect(0, 0, self.width, self.height, 2, fill=1, stroke=0)
        c.setFillColor(self.theme['accent'])
        c.rect(0, 0, 3.5 * mm, self.height, fill=1, stroke=0)

        c.setFillColor(S.WHITE)
        c.setFont('Helvetica-Bold', 18)
        c.drawString(9 * mm, self.height - 13 * mm, self.title)

        c.setFillColor(self.theme['accent'])
        c.setFont('Helvetica', 10)
        c.drawString(9 * mm, 7 * mm, 'NHIT • Infra.AI Platform')


class SummaryCard(Flowable):
    def __init__(self, value: str, label: str, theme: dict, width: float, height: float = 28 * mm):
        super().__init__()
        self.value = value
        self.label = label
        self.theme = theme
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.theme['card_bg'])
        c.roundRect(0, 0, self.width, self.height, 6, fill=1, stroke=0)

        c.setFillColor(self.theme['card_value'])
        c.setFont('Helvetica-Bold', 24)
        c.drawCentredString(self.width / 2, self.height / 2 + 1 * mm, str(self.value))

        c.setFillColor(self.theme['card_label'])
        c.setFont('Helvetica', 8)
        c.drawCentredString(self.width / 2, 5.5 * mm, self.label)


class ImageBox(Flowable):
    def __init__(self, path: str | None, width: float, height: float, theme: dict):
        super().__init__()
        self.path = path
        self.width = width
        self.height = height
        self.theme = theme

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.theme['image_bg'])
        c.roundRect(0, 0, self.width, self.height, 5, fill=1, stroke=0)

        if self.path:
            try:
                img = Image(self.path, width=self.width - 6, height=self.height - 6, kind='proportional')
                iw, ih = img.wrap(self.width, self.height)
                img.drawOn(c, (self.width - iw) / 2, (self.height - ih) / 2)
                return
            except Exception as exc:
                logger.warning('Image embed failed: %s', exc)

        c.setFillColor(S.WHITE)
        c.setFont('Helvetica', 11)
        c.drawCentredString(self.width / 2, self.height / 2 - 3, '[Image unavailable]')


class MetricPill(Flowable):
    def __init__(self, label: str, value: str, theme: dict, width: float, height: float = 10 * mm):
        super().__init__()
        self.label = label
        self.value = value
        self.theme = theme
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.theme['metric_bg'])
        c.roundRect(0, 0, self.width, self.height, self.height / 2, fill=1, stroke=0)
        c.setFillColor(S.WHITE)
        c.setFont('Helvetica', 8)
        c.drawString(4.5 * mm, self.height / 2 - 2.5, self.label)
        c.setFont('Helvetica-Bold', 8)
        c.drawRightString(self.width - 4.5 * mm, self.height / 2 - 2.5, str(self.value))


class DetailHeaderBar(Flowable):
    def __init__(self, number: int, title: str, timestamp: str, theme: dict, width: float):
        super().__init__()
        self.number = number
        self.title = title
        self.timestamp = timestamp
        self.theme = theme
        self.width = width
        self.height = 22 * mm

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.theme['detail_header_bg'])
        c.roundRect(0, 0, self.width, self.height, 6, fill=1, stroke=0)

        r = 6.5 * mm
        cx, cy = 11 * mm, self.height / 2
        c.setFillColor(self.theme['detail_circle'])
        c.circle(cx, cy, r, fill=1, stroke=0)
        c.setFillColor(S.WHITE)
        c.setFont('Helvetica-Bold', 13)
        c.drawCentredString(cx, cy - 4, str(self.number))

        c.setFillColor(S.WHITE)
        c.setFont('Helvetica-Bold', 15)
        c.drawString(21 * mm, self.height / 2 + 1.5 * mm, self.title[:50])
        c.setFillColor(colors.HexColor('#D1D5DB'))
        c.setFont('Helvetica', 8)
        c.drawString(21 * mm, self.height / 2 - 4.5 * mm, self.timestamp)

        badge = self.theme['detail_badge_text']
        bw, bh = 28 * mm, 7.5 * mm
        bx = self.width - bw - 5 * mm
        by = self.height / 2 - bh / 2
        c.setFillColor(self.theme['detail_badge'])
        c.roundRect(bx, by, bw, bh, 3.5, fill=1, stroke=0)
        c.setFillColor(S.WHITE)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(bx + bw / 2, by + 2.2, badge)


class BaseReportBuilder(ABC):
    title: str = ''
    type_field: str = ''
    total_label: str = ''
    types_label: str = ''
    breakdown_title: str = ''
    breakdown_type_header: str = ''
    theme: dict = S.DISTRESS

    def __init__(
        self,
        records: Sequence[Any],
        temp_dir: str,
        generated_at: datetime | None = None,
        *,
        prefetch: bool = True,
        prefetch_workers: int = IMAGE_PREFETCH_WORKERS,
        image_cache: dict[str, str | None] | None = None,
    ):
        self.records = list(records)
        self.temp_dir = temp_dir
        self.generated_at = generated_at or datetime.now()
        self.styles = S.get_styles()
        self._image_cache: dict[str, str | None] = dict(image_cache or {})
        self._prefetch = prefetch
        self._prefetch_workers = max(1, int(prefetch_workers))
        self.content_width = A4[0] - 2 * S.MARGIN

    def build(self, output_path: str) -> str:
        theme = self.theme
        generated_text = (
            f'Generated on {_fmt_datetime(self.generated_at)} • Infra.AI Road Inspection Platform'
        )

        # Optional up-front fetch (sync GET). Async large exports pass prefetch=False
        # and/or a pre-filled image_cache so images are prepared one report at a time.
        if self._prefetch:
            self._prefetch_record_images()

        doc = BaseDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=S.MARGIN,
            rightMargin=S.MARGIN,
            topMargin=12 * mm,
            bottomMargin=20 * mm,
            title=self.title,
            author='Infra.AI Road Inspection Platform',
        )

        frame = Frame(
            S.MARGIN, 20 * mm, self.content_width, A4[1] - 34 * mm,
            id='normal', showBoundary=0,
        )

        def on_cover(canvas, _doc):
            canvas.saveState()
            canvas.setFillColor(theme['cover_bg'])
            canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
            canvas.restoreState()

        def on_detail(canvas, _doc):
            canvas.saveState()
            canvas.setFillColor(S.WHITE)
            canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
            canvas.setStrokeColor(theme['detail_top_line'])
            canvas.setLineWidth(2.5)
            canvas.line(0, A4[1] - 1.5, A4[0], A4[1] - 1.5)
            canvas.restoreState()

        doc.addPageTemplates([
            PageTemplate(id='cover', frames=[frame], onPage=on_cover),
            PageTemplate(id='detail', frames=[frame], onPage=on_detail),
        ])

        story: list = list(self._build_cover())
        if self.records:
            story.append(NextPageTemplate('detail'))
            for index, record in enumerate(self.records, start=1):
                story.append(PageBreak())
                story.extend(self._build_detail(record, index))

        def make_canvas(*args, **kwargs):
            return NumberedCanvas(
                *args,
                footer_name=theme['footer_name'],
                generated_text=generated_text,
                theme=theme,
                **kwargs,
            )

        doc.build(story, canvasmaker=make_canvas)
        return output_path

    def _prefetch_record_images(self) -> None:
        urls = [
            (getattr(record, 'Image_URL', '') or '').strip()
            for record in self.records
            if (getattr(record, 'Image_URL', '') or '').strip() not in self._image_cache
        ]
        if not urls:
            return
        fetched = prefetch_images(
            urls,
            self.temp_dir,
            max_workers=self._prefetch_workers,
            optimize=True,
        )
        self._image_cache.update(fetched)

    def _build_cover(self) -> list:
        theme = self.theme
        w = self.content_width
        breakdown = count_by_field(self.records, self.type_field)
        section_style = self.styles['cover_section']

        return [
            Spacer(1, 4 * mm),
            BannerHeader(self.title, theme, w),
            Spacer(1, 10 * mm),
            self._summary_cards(w, breakdown),
            Spacer(1, 10 * mm),
            Paragraph(self.breakdown_title, section_style),
            self._breakdown_table(w, breakdown),
        ]

    def _summary_cards(self, width: float, breakdown: list) -> Table:
        gap = 4 * mm
        card_w = (width - 2 * gap) / 3
        cards = [
            SummaryCard(str(len(self.records)), self.total_label, self.theme, card_w),
            SummaryCard(str(len(breakdown)), self.types_label, self.theme, card_w),
            SummaryCard(_fmt_date_short(self.generated_at), 'Generated', self.theme, card_w),
        ]
        table = Table([cards], colWidths=[card_w + gap, card_w + gap, card_w])
        table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-2, -1), gap),
            ('RIGHTPADDING', (-1, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return table

    def _breakdown_table(self, width: float, breakdown: list) -> Table:
        header = [
            Paragraph(self.breakdown_type_header, self.styles['table_header']),
            Paragraph('Count', self.styles['table_header_center']),
        ]
        data = [header]
        for name, count in breakdown:
            data.append([
                Paragraph(self._escape(name), self.styles['table_cell_dark']),
                Paragraph(str(count), self.styles['table_cell_dark_center']),
            ])
        if len(data) == 1:
            data.append([
                Paragraph('—', self.styles['table_cell_dark']),
                Paragraph('0', self.styles['table_cell_dark_center']),
            ])

        table = Table(data, colWidths=[width * 0.75, width * 0.25], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), self.theme['table_header']),
            ('BACKGROUND', (0, 1), (-1, -1), self.theme['table_row_bg']),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#4B5563')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#4B5563')),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        return table

    def _build_detail(self, record: Any, number: int) -> list:
        theme = self.theme
        w = self.content_width
        title = str(getattr(record, self.type_field, '') or '—')
        ts = _fmt_record_timestamp(getattr(record, 'Date', ''))

        img_w = w * 0.52
        img_h = 75 * mm
        metric_w = w * 0.44
        image_url = (getattr(record, 'Image_URL', '') or '').strip()
        image_path = self._resolve_image(image_url)

        top = Table(
            [[ImageBox(image_path, img_w, img_h, theme), self._metrics_stack(record, metric_w)]],
            colWidths=[img_w + 5 * mm, metric_w],
        )
        top.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (0, 0), 5 * mm),
            ('RIGHTPADDING', (1, 0), (1, 0), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        elements: list = [
            Spacer(1, 2 * mm),
            DetailHeaderBar(number, title, ts, theme, w),
            Spacer(1, 5 * mm),
            top,
        ]
        if image_url:
            elements += [Spacer(1, 2 * mm), Paragraph(self._escape(image_url), self.styles['url'])]
        elements += [Spacer(1, 6 * mm), self._meta_table(record, w)]
        return elements

    def _metrics_stack(self, record: Any, width: float) -> Table:
        pills = [MetricPill(k, v, self.theme, width) for k, v in self._metric_pairs(record)]
        table = Table([[p] for p in pills], colWidths=[width])
        table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -2), 2.8 * mm),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 0),
        ]))
        return table

    def _meta_table(self, record: Any, width: float) -> Table:
        rows = [
            [
                Paragraph(label, self.styles['meta_label']),
                Paragraph(self._escape(str(value)), self.styles['meta_value']),
            ]
            for label, value in self._location_pairs(record)
        ]
        table = Table(rows, colWidths=[width * 0.28, width * 0.72])
        cmds = [
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]
        for i in range(len(rows)):
            cmds.append((
                'BACKGROUND', (0, i), (-1, i),
                S.WHITE if i % 2 == 0 else self.theme['table_alt'],
            ))
        table.setStyle(TableStyle(cmds))
        return table

    def _resolve_image(self, url: str) -> str | None:
        key = (url or '').strip()
        if not key:
            return None
        if key not in self._image_cache:
            # Lazy path: download+optimize one image at a time (async / memory-safe).
            self._image_cache[key] = download_image(key, self.temp_dir, optimize=True)
        return self._image_cache[key]

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    @abstractmethod
    def _metric_pairs(self, record: Any) -> list[tuple[str, str]]:
        ...

    @abstractmethod
    def _location_pairs(self, record: Any) -> list[tuple[str, str]]:
        ...


class DistressReportBuilder(BaseReportBuilder):
    title = 'Road Distress Analysis Report'
    type_field = 'Distress_Type'
    total_label = 'Total Detections'
    types_label = 'Distress Types'
    breakdown_title = 'Distress Breakdown'
    breakdown_type_header = 'Distress Type'
    theme = S.DISTRESS

    def _metric_pairs(self, record: Any) -> list[tuple[str, str]]:
        length = getattr(record, 'Length', 0) or 0
        width = getattr(record, 'Width', 0) or 0
        depth = getattr(record, 'Depth', 0) or 0
        return [
            ('Area', _area_m2(length, width)),
            ('Length', f'{length} m'),
            ('Width', f'{width} m'),
            ('Min Depth', '0 mm'),
            ('Max Depth', f'{_depth_mm(depth)} mm'),
        ]

    def _location_pairs(self, record: Any) -> list[tuple[str, str]]:
        return [
            ('Project', getattr(record, 'Project_Name', '') or '—'),
            ('Chainage', _chainage(getattr(record, 'Chainage_Start', 0), getattr(record, 'Chainage_End', 0))),
            ('Direction', getattr(record, 'Direction', '') or '—'),
            ('Lane', getattr(record, 'Lane', '') or '—'),
            ('Date', _fmt_record_date(getattr(record, 'Date', ''))),
            ('Latitude', getattr(record, 'Latitude', '')),
            ('Longitude', getattr(record, 'Longitude', '')),
        ]


class InventoryReportBuilder(BaseReportBuilder):
    title = 'Road Inventory Collection Report'
    type_field = 'Asset_type'
    total_label = 'Total Assets'
    types_label = 'Asset Types'
    breakdown_title = 'Asset Breakdown'
    breakdown_type_header = 'Asset Type'
    theme = S.INVENTORY

    def _metric_pairs(self, record: Any) -> list[tuple[str, str]]:
        video = (getattr(record, 'Video_URL', '') or '').strip()
        return [
            ('Sub-Asset', getattr(record, 'Sub_Asset_Type', '') or '—'),
            ('Carriage', getattr(record, 'Carriage_Type', '') or '—'),
            ('Lane', getattr(record, 'Lane', '') or '—'),
            ('Count', str(getattr(record, 'No_of_inventories', 0) or 0)),
            ('Video URL', 'Available' if video else 'Not Available'),
        ]

    def _location_pairs(self, record: Any) -> list[tuple[str, str]]:
        return [
            ('Project', getattr(record, 'Project_Name', '') or '—'),
            ('Chainage', _chainage(getattr(record, 'Chainage_start', 0), getattr(record, 'Chainage_end', 0))),
            ('Direction', getattr(record, 'Direction', '') or '—'),
            ('Lane', getattr(record, 'Lane', '') or '—'),
            ('Date', _fmt_record_date(getattr(record, 'Date', ''))),
            ('Latitude', getattr(record, 'Latitude', '')),
            ('Longitude', getattr(record, 'Longitude', '')),
        ]
