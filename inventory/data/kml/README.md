"""KML source files for chainage auto-fetch.

Stored here for import via:

  python manage.py import_kml_chainage "inventory/data/kml/Borkhedi to Wadner.kml" \\
      --project-name "Borkhedi to Wadner" --replace

API:

  POST /api/inventory/autofetch-chainage/
  {
    "project_name": "Borkhedi to Wadner",
    "latitude": 20.8462077,
    "longitude": 78.9606274
  }

  Response data fields: project_name, chainage_display, chainage_meters,
  distance_from_road_meters, nearest_latitude, nearest_longitude.
"""
