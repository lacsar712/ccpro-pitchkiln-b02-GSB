"""来脂批合并（改挂）业务规则。

主管把源来脂批并入目标来脂批：先把未收灶值守改挂到目标批，再删源批。

- 仅主管可发起（视图层校验 is_staff），值守工发起一律拒绝；
- 任一批在「出胶」相位灶上挂着未收灶值守 → 整次拒绝；
- 两批到货地不同时：以目标批（保留批）的到货地为准，源批到货地不再保留；
- 目标批到货千克累加源批到货量，来脂批卡片流总千克守恒（0 差）。
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.kiln.models import CookRun, FireHearth


def _drawing_open_run_for(lots):
    """返回给定批次中挂在「出胶」相位灶上的任意一条未收灶值守。"""
    return (
        CookRun.objects.filter(
            resinLot__in=lots,
            closedAt__isnull=True,
            hearth__phase=FireHearth.PHASE_DRAWING,
        )
        .select_related("hearth", "resinLot")
        .first()
    )


def assert_lots_mergeable(source, target) -> None:
    """合并前置校验：任一侧挂着出胶相位灶上的未收灶值守 → 整次拒绝。"""
    if source.pk == target.pk:
        raise ValidationError("源批与目标批不能是同一批。")

    blocked = _drawing_open_run_for((source, target))
    if blocked is not None:
        raise ValidationError(
            "无法合并：%(lot)s 在出胶灶「%(hearth)s」上仍有未收灶值守，整次合并已拒绝。"
            % {"lot": blocked.resinLot.lotCode, "hearth": blocked.hearth.tag}
        )


def merge_resin_lots(source, target) -> dict:
    """把源来脂批并入目标来脂批，返回合并摘要。

    顺序：先改挂未收灶值守（历史已收灶值守一并改挂，否则 PROTECT
    外键不允许删批），目标批千克累加迁入量，最后删除源批。
    到货地以目标批为准，源批到货地不保留。
    """
    assert_lots_mergeable(source, target)

    with transaction.atomic():
        moved_open = CookRun.objects.filter(
            resinLot=source, closedAt__isnull=True
        ).update(resinLot=target)
        moved_closed = CookRun.objects.filter(resinLot=source).update(
            resinLot=target
        )

        added_kg = source.arrivalKg
        target.arrivalKg = target.arrivalKg + added_kg
        target.save(update_fields=["arrivalKg"])

        source_code = source.lotCode
        source.delete()

    return {
        "source_code": source_code,
        "target_code": target.lotCode,
        "moved_open": moved_open,
        "moved_closed": moved_closed,
        "added_kg": added_kg,
        "target_kg": target.arrivalKg,
    }
