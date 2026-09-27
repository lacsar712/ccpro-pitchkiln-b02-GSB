"""来脂批合并业务规则。

合并方向：源批 → 目标批（源批并入目标批后删除）。
到货地规则：两批来源地不同时，一律保留目标批的 ``originPlace``，
不拼接、不覆盖——与来脂批页合并表单上的提示保持一致。
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.kiln.models import CookRun, FireHearth


def _has_drawing_open_run(lot) -> bool:
    """该批是否挂着「出胶」相位灶上的未收灶值守。"""
    return CookRun.objects.filter(
        resinLot=lot,
        closedAt__isnull=True,
        hearth__phase=FireHearth.PHASE_DRAWING,
    ).exists()


def merge_resin_lots(source, target):
    """
    把源来脂批并入目标来脂批：

    1. 任一侧（源批或目标批）挂着出胶相位灶上的未收灶值守 → 整次拒绝，
       不落任何改动；
    2. 源批名下全部值守（含未收灶与历史已收灶）改挂目标批；
    3. 目标批到货量(kg)累加源批到货量；
    4. 删除源批。来源地以目标批为准，不改动。

    返回目标批。
    """
    if source.pk == target.pk:
        raise ValidationError("源批与目标批不能是同一批。")

    if _has_drawing_open_run(source) or _has_drawing_open_run(target):
        raise ValidationError(
            "合并已拒绝：源批或目标批存在挂在出胶相位灶上的未收灶值守。"
        )

    with transaction.atomic():
        moved = CookRun.objects.filter(resinLot=source).update(resinLot=target)
        target.arrivalKg = target.arrivalKg + source.arrivalKg
        target.save(update_fields=["arrivalKg"])
        source.delete()

    target.merged_run_count = moved
    return target
