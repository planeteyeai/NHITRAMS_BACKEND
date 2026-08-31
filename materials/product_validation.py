"""Shared batch validation helpers for multi-product API payloads."""

from __future__ import annotations

from rest_framework import serializers

from materials.models import Material


def _product_list_error(index: int, field: str, message: str, *, total: int) -> serializers.ValidationError:
    product_errors = [{} for _ in range(total)]
    product_errors[index] = {field: [message]}
    return serializers.ValidationError(product_errors)


def validate_material_ids_in_products(products: list[dict]) -> dict[int, Material]:
    """Validate material_id on each product with a single Material query."""
    if not products:
        return {}

    material_ids: list[int] = []
    for index, item in enumerate(products):
        material_id = item.get('material_id')
        if material_id is None:
            raise _product_list_error(
                index,
                'material_id',
                'Material is required.',
                total=len(products),
            )
        try:
            parsed_id = int(material_id)
        except (TypeError, ValueError) as exc:
            raise _product_list_error(
                index,
                'material_id',
                'A valid integer is required.',
                total=len(products),
            ) from exc
        if parsed_id <= 0:
            raise _product_list_error(
                index,
                'material_id',
                'Material id must be positive.',
                total=len(products),
            )
        material_ids.append(parsed_id)

    unique_ids = set(material_ids)
    existing = Material.objects.filter(pk__in=unique_ids)
    material_map = {material.id: material for material in existing}
    missing = unique_ids - material_map.keys()
    if missing:
        missing_ids = set(missing)
        product_errors = [{} for _ in products]
        for index, material_id in enumerate(material_ids):
            if material_id in missing_ids:
                product_errors[index] = {'material_id': ['Material not found.']}
        raise serializers.ValidationError(product_errors)
    return material_map
