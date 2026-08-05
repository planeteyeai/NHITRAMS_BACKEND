"""Routine Maintenance masters from Ref_Routine Maintenance Excel (Annexure-I).

Source of truth: RAMS APK workbook sheet `Ref_Routine Maintenance`.
Keep long names exactly as in Excel — do not shorten.
Re-run `_extract_rm_masters.py` / this generator to refresh.
"""

RM_CATEGORY_SEED = [
    (1, 'Median Maintenance'),
    (2, 'Road Side Maintenance/ RoW (Avenue till boundary pillar)'),
    (3, 'Maintenance and watering of Existing plants (Median + Avenue)'),
    (4, 'Cleaning of Road furniture (Delineators, signboards, MBCB, PGR, Gantry Boards, KM, HM, 5th KM Stones, Guard post & Boundary pillars, Kerb etc.,)'),
    (5, 'Cleaning of Road (MCW/SR)'),
    (6, 'Cleaning of Drains'),
    (7, 'Cleaning of Toll Plaza and Booths (from outside)'),
    (8, 'Cleaning of waterway of structures/Structures'),
    (9, 'Safety during service'),
]

RM_SUBCATEGORY_SEED = {
    'Median Maintenance': [
        'Weeds/Unwanted Vegetation removal in the median including median chutes',
        'Weeds/Unwanted Vegetation removal in separator including  chutes',
        'Trimming, Pruning of Median plants',
        'Basin making for Median plants',
        'Apply-Pesticide & Manure-Median Plants',
    ],
    'Road Side Maintenance/ RoW (Avenue till boundary pillar)': [
        'Weeds/Unwanted Vegetation -Cleaning from Earthen shoulder to ROW (Including Unlined Drain High Embankment, Side slope chute drain, Stone Pitching, Quarter Coning in structures, MBCB weed removal, & RE wall unpaved Separators) (2 Lane Consideration)',
        'Earthen shoulder levelling excluding soil',
    ],
    'Maintenance and watering of Existing plants (Median + Avenue)': [
        'Watering for Median plants (Considering 10 months for the year and 2 Months Monsoon)',
        'Replacement of causality -Median plants',
        'Watering for Avenue plants (Considering 10 months for the year and 2 Months Monsoon)',
        'Trimming, Pruning of Avenue Plantation',
        'Basin making for Avenue plants (Considering 10 months for the year and 2 Months Monsoon)',
        'Replacement of causality -Avenue plants',
        'Apply-Pesticide & Manure-Avenue Plants',
    ],
    'Cleaning of Road furniture (Delineators, signboards, MBCB, PGR, Gantry Boards, KM, HM, 5th KM Stones, Guard post & Boundary pillars, Kerb etc.,)': [
        'Cleaning - sign Board- including Toll Plaza Sign Boards, single post & Double Post, Gantry sign Boards',
        'Cleaning of Delineators & Solar Blinkers',
        'Cleaning/ Weed Removal below PGRs',
        'Cleaning Distance Marking stone- Km stone/Hectometre stone/5th KM Stone.',
        'Cleaning of ROW Pillar/Guard Post Etc,.',
        'Cleaning (Washing) of MBCB in Rural/Urban areas including Fixing of Reflective stickers (If Required)',
        'Cleaning(Washing) of PGR',
        'Cleaning(Washing) of Kerb',
    ],
    'Cleaning of Road (MCW/SR)': [
        'MCW- Flexible/Rigid pavement - Cleaning & Sweeping in Rural area including Foot path & drainage spouts of structures; truck lay byes;etc (2 Lane Consideration)',
        'MCW- Flexible/Rigid pavement - Cleaning & Sweeping Median Opening- Rural area',
        'Cleaning & Sweeping of Minor Junction/Intersection on Flexible/Rigid Pavement- Rural Area',
        'Cleaning & Sweeping of Bus Bay & Bus Shelter on Flexible & Rigid Pavement- Rural Area',
        'MCW- Flexible/Rigid pavement - Cleaning & Sweeping in Urban area including Foot path of structures & drainage spouts ; truck lay byes; bus bays; etc (2 Lane Consideration)',
        'MCW- Flexible/Rigid pavement - Cleaning & Sweeping - Median Opening- Urban Area',
        'Cleaning & Sweeping of Minor Junction/Intersection on Flexible/Rigid Pavement- Urban Area',
        'Cleaning & Sweeping of Bus Bay & Bus Shelter on Flexible & Rigid Pavement- Urban Area',
        'SR- Flexible pavement Cleaning & Sweeping in Urban area including RE wall Paved Separators & Drain cum Foot path and Underpasses ( Entry and exist length shall not consider in this length)',
        'Clean, Sweep of Major Junction/Intersection - Rural Area & Urban Area',
        'Toll Plaza PQC - (LHS & RHS)',
    ],
    'Cleaning of Drains': [
        'Cleaning & vegetation removal - Lined Drain',
        'Cleaning & vegetation removal - Median chute drain',
        'Cleaning & vegetation removal - Median Longitudinal drain',
    ],
    'Cleaning of Toll Plaza and Booths (from outside)': [
        'Cleaning of External Paved Developed Areas and Maintenance of Toll plaza external landscape area  (Toll Plaza Premises, Highway Nest, Medical aid post, Plaza building, Maintenance yards, SWBs; etc)',
        'Cleaning & Washing of Booths & Lanes',
        'Cleaning & Washing of Toll Plaza Canopies',
        'Truck Lay Bye & Toilet Cleaning',
    ],
    'Cleaning of waterway of structures/Structures': [
        'Box/Slab culvert-Vent Cleaning including silt removal (Single Structure considered shall be 1 Nos)',
        'Pipe culvert-Vent Cleaning including silt removal (Single Structure considered shall be 1 Nos)',
        'Minor Bridge -Vent Cleaning including silt removal (Single Structure considered shall be 1 Nos)',
        'Expansion Joint cleaning of Structure (Major Bridge, Minor Bridge, Flyovers, ROB/RUB Etc,) ( 1 Structure=1 Nos)',
        'Drainage sport cleaning of Structure (Major Bridge, Minor Bridge, Flyovers, ROB/RUB Etc,) ( 1 Structure=1 Nos)',
        'Cleaning/Washing of Parapet wall , Crash barrier & Handrails (Culverts, Major Bridge, Minor Bridge, Flyovers,Underpasses,ROB/RUB Etc,)( 1 Structure=1 Nos)',
        'Vegetation removal from RE Wall(1 Location=1 Nos)',
    ],
    'Safety during service': [
        'The Work zone shall be Barricaded as per Prevailing IRC Code and standard & NHIT Safety SOP',
    ],
}

# Side / severity are shared with inventory masters (see seed_inventory_masters).
