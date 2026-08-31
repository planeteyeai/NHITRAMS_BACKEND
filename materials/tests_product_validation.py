"""Query-count tests for batch material validation."""

from django.test import TestCase

from materials.models import Material
from materials.product_validation import validate_material_ids_in_products


def _material(name: str) -> Material:
    return Material.objects.create(
        material_name=name,
        material_unit='Meter',
        material_type='Electrical',
        other_type='',
        material_description='',
        created_by='system',
        updated_by='system',
    )


class BatchMaterialValidationTests(TestCase):
    def setUp(self):
        self.materials = [_material(f'Material {index}') for index in range(10)]

    def test_ten_products_use_single_material_query(self):
        products = [{'material_id': material.id} for material in self.materials]
        with self.assertNumQueries(1):
            material_map = validate_material_ids_in_products(products)
        self.assertEqual(len(material_map), 10)

    def test_duplicate_material_ids_still_use_single_query(self):
        products = [{'material_id': self.materials[0].id} for _ in range(10)]
        with self.assertNumQueries(1):
            material_map = validate_material_ids_in_products(products)
        self.assertEqual(len(material_map), 1)
