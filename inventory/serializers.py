import json

from rest_framework import serializers

from .models import DPRReport, InventoryReport


class InventoryReportWriteSerializer(serializers.ModelSerializer):
    """Accepts the exact payload used by the legacy Excel API.

    Username is intentionally excluded — it is set from the JWT in the view.
    """

    class Meta:
        model = InventoryReport
        fields = [
            'Project_Name',
            'Chainage_start',
            'Chainage_end',
            'Direction',
            'Asset_type',
            'Latitude',
            'Longitude',
            'Date',
            'Sub_Asset_Type',
            'Carriage_Type',
            'Lane',
            'No_of_inventories',
            'Image_URL',
            'Video_URL',
            'Source',
        ]
        extra_kwargs = {
            'Project_Name': {'required': True, 'allow_blank': False},
            'Chainage_start': {'required': True},
            'Chainage_end': {'required': True},
            'Direction': {'required': True, 'allow_blank': False},
            'Asset_type': {'required': True, 'allow_blank': False},
            'Latitude': {'required': True},
            'Longitude': {'required': True},
            'Date': {'required': True, 'allow_blank': False},
            'Sub_Asset_Type': {'required': False, 'allow_blank': True},
            'Carriage_Type': {'required': False, 'allow_blank': True},
            'Lane': {'required': False, 'allow_blank': True},
            'No_of_inventories': {'required': False},
            'Image_URL': {'required': False, 'allow_blank': True},
            'Video_URL': {'required': False, 'allow_blank': True},
            'Source': {'required': False, 'allow_blank': True},
        }

    def validate(self, data):
        instance = getattr(self, 'instance', None)
        chainage_start = data.get(
            'Chainage_start',
            getattr(instance, 'Chainage_start', None) if instance is not None else None,
        )
        chainage_end = data.get(
            'Chainage_end',
            getattr(instance, 'Chainage_end', None) if instance is not None else None,
        )
        if (
            chainage_start is not None
            and chainage_end is not None
            and chainage_start > chainage_end
        ):
            raise serializers.ValidationError(
                {'Chainage_end': 'Chainage_end must be greater than or equal to Chainage_start.'}
            )
        return data


class InventoryReportReadSerializer(serializers.ModelSerializer):
    """Full report representation preserving legacy field names."""

    class Meta:
        model = InventoryReport
        fields = [
            'id',
            'username',
            'Project_Name',
            'Chainage_start',
            'Chainage_end',
            'Direction',
            'Asset_type',
            'Latitude',
            'Longitude',
            'Date',
            'Sub_Asset_Type',
            'Carriage_Type',
            'Lane',
            'No_of_inventories',
            'Image_URL',
            'Video_URL',
            'Source',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class InventoryFilterSerializer(serializers.Serializer):
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    date = serializers.DateField(format='%Y-%m-%d', input_formats=['%Y-%m-%d'])
    direction = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )
    project_name = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )
    asset_type = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )

    def validate(self, data):
        if data['chainage_start'] > data['chainage_end']:
            raise serializers.ValidationError("chainage_start must be less than or equal to chainage_end")
        return data

class InventoryResponseSerializer(serializers.Serializer):
    project_name = serializers.CharField()
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    direction = serializers.CharField()
    asset_type = serializers.CharField()
    trees = serializers.FloatField()
    culvert = serializers.FloatField()
    street_lights = serializers.FloatField()
    bridges = serializers.FloatField()
    traffic_signals = serializers.FloatField()
    bus_stop = serializers.FloatField()
    truck_layby = serializers.FloatField()
    toll_plaza = serializers.FloatField()
    adjacent_road = serializers.FloatField()
    toilet_blocks = serializers.FloatField()
    rest_area = serializers.FloatField()
    rcc_drain = serializers.FloatField()
    fuel_station = serializers.FloatField()
    emergency_call_box = serializers.FloatField()
    tunnels = serializers.FloatField()
    footpath = serializers.FloatField()
    junction = serializers.FloatField()
    sign_boards = serializers.FloatField()
    solar_blinker = serializers.FloatField()
    median_plants = serializers.FloatField()
    service_road = serializers.FloatField()
    km_stones = serializers.FloatField()
    crash_barrier = serializers.FloatField()
    median_opening = serializers.CharField()
    kerb = serializers.FloatField()
    guard_post = serializers.FloatField()
    delineators = serializers.FloatField()
    pedestrian_guard_rail = serializers.FloatField()
    slip_roads = serializers.FloatField()
    bus_bays = serializers.FloatField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    date = serializers.DateField()
    sub_asset_type = serializers.CharField(allow_null=True)
    carriage_type = serializers.CharField()
    lane = serializers.CharField()
    image_url = serializers.CharField(allow_blank=True)
    source = serializers.CharField(allow_blank=True)


# Fields the client must never supply — resolved on the backend only.
DPR_SERVER_OWNED_FIELDS = frozenset({
    'username',
    'project_name',
    'start_chainage',
    'end_chainage',
    'total_project_length',
})


def _parse_optional_json_object(value, field_name: str):
    """Parse a dict or JSON-object string. Empty values become {}."""
    if value is None or value == '':
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise serializers.ValidationError(
                {field_name: f'Invalid JSON for {field_name}: {exc.msg}.'}
            ) from exc
        if not isinstance(parsed, dict):
            raise serializers.ValidationError(
                {field_name: f'{field_name} must be a JSON object.'}
            )
        return parsed
    raise serializers.ValidationError(
        {field_name: f'{field_name} must be a JSON object or JSON string.'}
    )


def _parse_non_negative_int(value, *, field_path: str):
    """Coerce a quantity value to a non-negative int."""
    if value is None or value == '':
        raise serializers.ValidationError({field_path: 'This field is required.'})
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise serializers.ValidationError({field_path: 'Must be an integer.'})
    if isinstance(value, float) and not value.is_integer():
        raise serializers.ValidationError({field_path: 'Must be an integer.'})
    if isinstance(value, str):
        raw = value.strip()
        if not raw or not raw.lstrip('-').isdigit():
            raise serializers.ValidationError({field_path: 'Must be an integer.'})
        value = int(raw)
    else:
        value = int(value)
    if value < 0:
        raise serializers.ValidationError({field_path: 'Must be greater than or equal to 0.'})
    return value


def normalize_overall_summary(raw_summary):
    """Validate asset quantities and attach available_quantity.

    Expected shape per asset key:
        {"total_quantity": int, "damaged_quantity": int}
    Stored shape:
        {"total_quantity": int, "damaged_quantity": int, "available_quantity": int}
    """
    if raw_summary is None or raw_summary == '':
        return {}

    summary = _parse_optional_json_object(raw_summary, 'overall_summary')
    if not summary:
        return {}

    normalized = {}
    asset_errors = {}

    for asset_key, asset_data in summary.items():
        if not isinstance(asset_data, dict):
            asset_errors[asset_key] = (
                'Must be an object with total_quantity and damaged_quantity.'
            )
            continue

        field_errors = {}
        total_quantity = None
        damaged_quantity = None

        try:
            total_quantity = _parse_non_negative_int(
                asset_data.get('total_quantity'),
                field_path='total_quantity',
            )
        except serializers.ValidationError as exc:
            detail = exc.detail
            field_errors['total_quantity'] = detail.get('total_quantity', detail)

        try:
            damaged_quantity = _parse_non_negative_int(
                asset_data.get('damaged_quantity'),
                field_path='damaged_quantity',
            )
        except serializers.ValidationError as exc:
            detail = exc.detail
            field_errors['damaged_quantity'] = detail.get('damaged_quantity', detail)

        if not field_errors:
            if damaged_quantity > total_quantity:
                field_errors['damaged_quantity'] = (
                    'damaged_quantity cannot be greater than total_quantity.'
                )
            else:
                normalized[asset_key] = {
                    'total_quantity': total_quantity,
                    'damaged_quantity': damaged_quantity,
                    'available_quantity': total_quantity - damaged_quantity,
                }

        if field_errors:
            asset_errors[asset_key] = field_errors

    if asset_errors:
        raise serializers.ValidationError({'overall_summary': asset_errors})

    return normalized


def _parse_url_list(value, field_name: str) -> list[str]:
    """Parse a JSON array (or Python list) of image URL strings."""
    if value is None or value == '':
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise serializers.ValidationError(
                {field_name: f'Invalid JSON for {field_name}: {exc.msg}.'}
            ) from exc
    if not isinstance(value, list):
        raise serializers.ValidationError(
            {field_name: f'{field_name} must be a JSON array of URL strings.'}
        )
    urls = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise serializers.ValidationError(
                {field_name: f'{field_name}[{index}] must be a non-empty URL string.'}
            )
        urls.append(item.strip())
    return urls


def resolve_dpr_uploaded_images(
    *,
    existing: list | None,
    new_urls: list[str],
    keep_images=None,
    remove_images=None,
    clear_images: bool = False,
    partial: bool = True,
    has_new_files: bool = False,
) -> list[str]:
    """Compute the final uploaded_images list for PUT/PATCH.

    Semantics:
    - No image-related input → leave existing unchanged.
    - clear_images=true → start from [].
    - keep_images / retain_image_urls provided → start from that list.
    - PUT + new files (no keep list) → replace with new uploads only.
    - PATCH + new files (no keep list) → append to existing.
    - remove_images → drop those URLs from the base list (DB refs only; no S3 delete).
    - New uploads are then appended (deduped).
    """
    image_touch = (
        clear_images
        or keep_images is not None
        or remove_images is not None
        or has_new_files
    )
    if not image_touch:
        return list(existing or [])

    if clear_images:
        base: list[str] = []
    elif keep_images is not None:
        base = list(keep_images)
    elif not partial and has_new_files:
        base = []
    else:
        base = list(existing or [])

    if remove_images:
        remove_set = set(remove_images)
        base = [url for url in base if url not in remove_set]

    result = list(base)
    for url in new_urls or []:
        if url not in result:
            result.append(url)
    return result


def _extract_retain_image_urls(data) -> tuple[bool, object]:
    """Return (present, value) for keep_images or retain_image_urls."""
    if not hasattr(data, 'keys'):
        return False, None
    keys = set(data.keys())
    if 'keep_images' in keys:
        return True, data.get('keep_images')
    if 'retain_image_urls' in keys:
        return True, data.get('retain_image_urls')
    # Case-insensitive fallback for multipart quirks
    lower_map = {str(k).lower(): k for k in keys}
    if 'keep_images' in lower_map:
        return True, data.get(lower_map['keep_images'])
    if 'retain_image_urls' in lower_map:
        return True, data.get(lower_map['retain_image_urls'])
    return False, None


class DPRReportWriteSerializer(serializers.Serializer):
    """Accepts multipart DPR payload. Identity/project fields are rejected."""

    report_date = serializers.DateField(input_formats=['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'])
    reporting_engineer = serializers.CharField(max_length=255, trim_whitespace=True)
    assessment_date = serializers.DateField(
        required=False,
        allow_null=True,
        input_formats=['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'],
    )
    remarks = serializers.CharField(required=False, allow_blank=True, default='')
    asset_details = serializers.JSONField(required=False, default=dict)
    overall_summary = serializers.JSONField(required=False, default=dict)
    status = serializers.ChoiceField(
        choices=DPRReport.STATUS_CHOICES,
        required=False,
        default=DPRReport.STATUS_SUBMITTED,
    )
    # Image management (used by PUT/PATCH; ignored on create except unused)
    keep_images = serializers.JSONField(required=False, allow_null=True)
    remove_images = serializers.JSONField(required=False, allow_null=True)
    clear_images = serializers.BooleanField(required=False, default=False)

    def to_internal_value(self, data):
        # Reject identity / project fields early (multipart QueryDict or dict).
        if hasattr(data, 'keys'):
            incoming = {str(key).lower() for key in data.keys()}
        else:
            incoming = set()
        blocked = incoming & {field.lower() for field in DPR_SERVER_OWNED_FIELDS}
        if blocked:
            raise serializers.ValidationError(
                {field: 'This field is resolved on the server and must not be sent.'
                 for field in sorted(blocked)}
            )

        mutable = {}
        keys_present = set(data.keys()) if hasattr(data, 'keys') else set()

        for key in (
            'report_date',
            'reporting_engineer',
            'assessment_date',
            'remarks',
            'asset_details',
            'overall_summary',
            'status',
            'clear_images',
        ):
            if key not in keys_present:
                continue
            value = data.get(key)
            if value is not None and value != '':
                mutable[key] = value

        # Explicit empty arrays are valid for keep/remove (e.g. keep_images=[]).
        if 'remove_images' in keys_present:
            mutable['remove_images'] = data.get('remove_images')

        retain_present, retain_value = _extract_retain_image_urls(data)
        if retain_present:
            mutable['keep_images'] = retain_value

        if 'asset_details' in mutable and isinstance(mutable['asset_details'], str):
            raw = mutable['asset_details'].strip()
            if not raw:
                mutable['asset_details'] = {}
            else:
                try:
                    mutable['asset_details'] = json.loads(raw)
                except json.JSONDecodeError:
                    mutable['asset_details'] = {'raw': raw}

        if 'overall_summary' in mutable:
            mutable['overall_summary'] = _parse_optional_json_object(
                mutable['overall_summary'],
                'overall_summary',
            )

        if 'keep_images' in mutable:
            mutable['keep_images'] = _parse_url_list(mutable['keep_images'], 'keep_images')
        if 'remove_images' in mutable:
            mutable['remove_images'] = _parse_url_list(mutable['remove_images'], 'remove_images')

        if 'clear_images' in mutable:
            raw_clear = mutable['clear_images']
            if isinstance(raw_clear, str):
                mutable['clear_images'] = raw_clear.strip().lower() in {
                    '1', 'true', 'yes', 'on',
                }

        return super().to_internal_value(mutable)

    def validate_reporting_engineer(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('reporting_engineer is required.')
        return value.strip()

    def validate_overall_summary(self, value):
        return normalize_overall_summary(value)


class DPRReportReadSerializer(serializers.ModelSerializer):
    """Full DPR representation including auto-fetched project fields and S3 URLs."""

    report_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = DPRReport
        fields = [
            'report_id',
            'username',
            'project_name',
            'start_chainage',
            'end_chainage',
            'total_project_length',
            'report_date',
            'reporting_engineer',
            'assessment_date',
            'remarks',
            'asset_details',
            'overall_summary',
            'report_data',
            'uploaded_images',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields