"""Cache invalidation signals for managed write models."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from nhit.caching.invalidation import (
    invalidate_inventory_master_cache,
    invalidate_labour_masters_cache,
    invalidate_materials_list_cache,
    invalidate_supplier_masters_cache,
)


def _connect_material_signals():
    try:
        from materials.models import Material
    except Exception:
        return

    @receiver(post_save, sender=Material)
    @receiver(post_delete, sender=Material)
    def _invalidate_materials_cache(**kwargs):
        invalidate_materials_list_cache()


def _connect_supplier_signals():
    try:
        from supplier.models import Supplier
    except Exception:
        return

    @receiver(post_save, sender=Supplier)
    @receiver(post_delete, sender=Supplier)
    def _invalidate_supplier_cache(**kwargs):
        invalidate_supplier_masters_cache()


def _connect_labour_signals():
    try:
        from labour.models import Labour
    except Exception:
        return

    @receiver(post_save, sender=Labour)
    @receiver(post_delete, sender=Labour)
    def _invalidate_labour_cache(**kwargs):
        invalidate_labour_masters_cache()


def _connect_inventory_report_signals():
    try:
        from inventory.models import InventoryReport
    except Exception:
        return

    @receiver(post_save, sender=InventoryReport)
    @receiver(post_delete, sender=InventoryReport)
    def _invalidate_inventory_master_cache(**kwargs):
        invalidate_inventory_master_cache()


def _connect_distress_repair_signals():
    try:
        from distress.models import DistressRepairLog
    except Exception:
        return

    from nhit.caching.invalidation import invalidate_distress_report_filter_cache

    @receiver(post_save, sender=DistressRepairLog)
    @receiver(post_delete, sender=DistressRepairLog)
    def _invalidate_distress_filter_cache(**kwargs):
        invalidate_distress_report_filter_cache()


def connect_cache_invalidation_signals() -> None:
    _connect_material_signals()
    _connect_supplier_signals()
    _connect_labour_signals()
    _connect_inventory_report_signals()
    _connect_distress_repair_signals()
