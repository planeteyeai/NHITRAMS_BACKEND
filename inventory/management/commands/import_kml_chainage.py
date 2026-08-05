"""Import KML placemarks into ChainageReferencePoint.

Example:
  python manage.py import_kml_chainage "inventory/data/kml/Borkhedi to Wadner.kml" \\
      --project-name "Borkhedi to Wadner" --replace
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import ChainageReferencePoint

BATCH_SIZE = 5000


def _local(tag: str) -> str:
    if '}' in tag:
        return tag.rsplit('}', 1)[-1]
    return tag


def _text(element) -> str:
    if element is None or element.text is None:
        return ''
    return element.text.strip()


def _first_child(elem, local_name: str):
    for child in elem:
        if _local(child.tag) == local_name:
            return child
    return None


def _parse_placemark(elem):
    """Return (chainage_meters, lat, lng) or None."""
    name_el = _first_child(elem, 'name')
    chainage_raw = _text(name_el)
    if not chainage_raw:
        return None

    lat = None
    lng = None
    extended = _first_child(elem, 'ExtendedData')
    if extended is not None:
        for node in extended.iter():
            if _local(node.tag) != 'SimpleData':
                continue
            field = node.attrib.get('name', '').strip().lower()
            value = _text(node)
            if field == 'lat':
                lat = value
            elif field in ('long', 'lng', 'lon', 'longitude'):
                lng = value

    if lat is None or lng is None:
        point = _first_child(elem, 'Point')
        coords_el = _first_child(point, 'coordinates') if point is not None else None
        coords = _text(coords_el)
        if coords:
            parts = coords.split(',')
            if len(parts) >= 2:
                lng = parts[0].strip()
                lat = parts[1].strip()

    if lat is None or lng is None:
        return None

    try:
        return float(chainage_raw), float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def iter_placemark_points(kml_path: Path):
    """Yield (chainage_meters, latitude, longitude) from KML Placemarks."""
    context = ET.iterparse(str(kml_path), events=('end',))
    for _event, elem in context:
        if _local(elem.tag) != 'Placemark':
            continue
        parsed = _parse_placemark(elem)
        elem.clear()
        if parsed is not None:
            yield parsed


class Command(BaseCommand):
    help = 'Import chainage GPS reference points from a KML file.'

    def add_arguments(self, parser):
        parser.add_argument(
            'kml_path',
            type=str,
            help='Path to the KML file (absolute or relative to project root).',
        )
        parser.add_argument(
            '--project-name',
            required=True,
            help='Project / corridor label stored with each point.',
        )
        parser.add_argument(
            '--replace',
            action='store_true',
            help='Delete existing points for this project_name before import.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=BATCH_SIZE,
            help=f'Bulk insert batch size (default {BATCH_SIZE}).',
        )

    def handle(self, *args, **options):
        kml_path = Path(options['kml_path']).expanduser()
        if not kml_path.is_absolute():
            kml_path = Path.cwd() / kml_path
        if not kml_path.exists():
            raise CommandError(f'KML file not found: {kml_path}')

        project_name = options['project_name'].strip()
        if not project_name:
            raise CommandError('--project-name must not be empty.')

        batch_size = options['batch_size']
        if batch_size < 1:
            raise CommandError('--batch-size must be >= 1.')

        if options['replace']:
            deleted, _ = ChainageReferencePoint.objects.filter(
                project_name=project_name
            ).delete()
            self.stdout.write(
                f'Deleted {deleted} existing points for "{project_name}".'
            )

        batch = []
        total = 0
        self.stdout.write(f'Importing from {kml_path} ...')

        for chainage_meters, latitude, longitude in iter_placemark_points(kml_path):
            batch.append(
                ChainageReferencePoint(
                    project_name=project_name,
                    chainage_meters=chainage_meters,
                    chainage_km=chainage_meters / 1000.0,
                    latitude=latitude,
                    longitude=longitude,
                )
            )
            if len(batch) >= batch_size:
                with transaction.atomic():
                    ChainageReferencePoint.objects.bulk_create(batch, batch_size=batch_size)
                total += len(batch)
                self.stdout.write(f'  inserted {total} ...')
                batch = []

        if batch:
            with transaction.atomic():
                ChainageReferencePoint.objects.bulk_create(batch, batch_size=batch_size)
            total += len(batch)

        if total == 0:
            raise CommandError('No placemarks were imported from the KML file.')

        self.stdout.write(self.style.SUCCESS(
            f'Imported {total} chainage reference points for "{project_name}".'
        ))
