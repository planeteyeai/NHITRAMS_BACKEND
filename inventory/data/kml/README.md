"""KML source files for chainage auto-fetch.

Stored here for import via:

  python manage.py import_kml_chainage "inventory/data/kml/Borkhedi to Wadner.kml" \\
      --project-name "Borkhedi to Wadner" --replace

  python manage.py import_kml_chainage "inventory/data/kml/Kothakota-Kurnool.kml" \\
      --project-name "Kothakota to Kurnool" --replace

API:

  POST /api/inventory/autofetch-chainage/
  {
    "project_name": "Borkhedi to Wadner",
    "latitude": 20.8462077,
    "longitude": 78.9606274
  }

  or

  {
    "project_name": "Kothakota to Kurnool",
    "latitude": 16.3616922878,
    "longitude": 77.9434110569
  }

  Response data fields: project_name, chainage_display, chainage_meters,
  distance_from_road_meters, nearest_latitude, nearest_longitude.
"""
