from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot
from .services.lot_merge import merge_resin_lots


def make_lot(code, place, kg, days_ago=1):
    return ResinLot.objects.create(
        lotCode=code,
        originPlace=place,
        arrivalKg=Decimal(kg),
        receivedAt=timezone.now() - timezone.timedelta(days=days_ago),
    )


def make_hearth(tag, phase=FireHearth.PHASE_HOLDING, lane=1):
    return FireHearth.objects.create(
        lane=lane, tag=tag, resinGrade="特级脂", phase=phase
    )


def make_run(hearth, lot, closed=False):
    return CookRun.objects.create(
        hearth=hearth,
        resinLot=lot,
        openedAt=timezone.now() - timezone.timedelta(hours=5),
        closedAt=timezone.now() if closed else None,
        targetSoftPointC=Decimal("88.00"),
    )


class MergeBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser(
            "admin", "admin@pitchkiln.local", "123456"
        )
        cls.worker = User.objects.create_user(
            "worker", "worker@pitchkiln.local", "123456"
        )

    def setUp(self):
        # 源批：两条未收灶 + 一条已收灶历史
        self.source = make_lot("脂-源-001", "松脂坳东沟", "1000.00")
        self.h_src1 = make_hearth("源灶-甲", FireHearth.PHASE_HOLDING)
        self.h_src2 = make_hearth("源灶-乙", FireHearth.PHASE_CHARGING)
        self.h_src3 = make_hearth("源灶-丙", FireHearth.PHASE_COLD)
        self.run_open1 = make_run(self.h_src1, self.source)
        self.run_open2 = make_run(self.h_src2, self.source)
        self.run_closed = make_run(self.h_src3, self.source, closed=True)
        # 目标批：一条未收灶
        self.target = make_lot("脂-目标-001", "桐油坑北坡", "500.50", days_ago=2)
        self.h_tgt = make_hearth("目标灶-甲", FireHearth.PHASE_RAMPING)
        self.run_target = make_run(self.h_tgt, self.target)

    def post_merge(self, source=None, target=None, **kw):
        return self.client.post(
            reverse("resin_lot_merge"),
            {
                "source": (source or self.source).pk,
                "target": (target or self.target).pk,
            },
            **kw,
        )

    def merge_and_flush(self, **kw):
        """合并后消费掉成功 toast，避免提示里的源批号干扰页面断言。"""
        self.post_merge(**kw)
        self.client.get(reverse("home"))


class MergeSuccessTests(MergeBase):
    def test_merge_moves_runs_sums_kg_and_deletes_source(self):
        self.client.force_login(self.admin)
        resp = self.post_merge()
        self.assertRedirects(resp, reverse("resin_lot_feed"))

        # 未收灶值守改挂目标批（含已收灶历史一并迁移）
        for run in (self.run_open1, self.run_open2, self.run_closed):
            run.refresh_from_db()
            self.assertEqual(run.resinLot_id, self.target.pk)
        # 源批已删除，目标批千克累加迁入量
        self.assertFalse(ResinLot.objects.filter(pk=self.source.pk).exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.arrivalKg, Decimal("1500.50"))

    def test_origin_place_target_wins(self):
        self.client.force_login(self.admin)
        self.post_merge()
        self.target.refresh_from_db()
        self.assertEqual(self.target.originPlace, "桐油坑北坡")

    def test_source_detail_404_target_detail_lists_runs(self):
        self.client.force_login(self.admin)
        source_pk = self.source.pk
        self.merge_and_flush()
        # 源批详情打不开
        self.assertEqual(
            self.client.get(reverse("resin_lot_detail", args=[source_pk])).status_code,
            404,
        )
        # 按批筛值守只见目标批
        resp = self.client.get(reverse("resin_lot_detail", args=[self.target.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "脂-目标-001")
        self.assertEqual(resp.context["runs"].count(), 4)
        self.assertNotContains(resp, "脂-源-001")

    def test_feed_shows_only_target_with_summed_kg(self):
        self.client.force_login(self.admin)
        self.merge_and_flush()
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, "脂-目标-001")
        self.assertContains(resp, "1500.50")
        self.assertNotContains(resp, "脂-源-001")

    def test_board_and_drawer_show_target_lot_code(self):
        self.client.force_login(self.admin)
        self.merge_and_flush()
        board = self.client.get(reverse("home"))
        self.assertContains(board, "脂-目标-001")
        self.assertNotContains(board, "脂-源-001")
        drawer = self.client.get(
            reverse("hearth_drawer", args=[self.h_src1.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(drawer, "脂-目标-001")
        self.assertNotContains(drawer, "脂-源-001")

    def test_open_run_form_lot_choices_only_target(self):
        self.client.force_login(self.admin)
        cold = make_hearth("冷灶-丁", FireHearth.PHASE_COLD)
        self.merge_and_flush()
        resp = self.client.get(
            reverse("hearth_drawer", args=[cold.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(resp, "脂-目标-001")
        self.assertNotContains(resp, "脂-源-001")


class MergePermissionTests(MergeBase):
    def test_worker_merge_always_rejected(self):
        self.client.force_login(self.worker)
        resp = self.post_merge()
        self.assertEqual(resp.status_code, 403)
        # 零副作用
        self.assertTrue(ResinLot.objects.filter(pk=self.source.pk).exists())
        self.run_open1.refresh_from_db()
        self.assertEqual(self.run_open1.resinLot_id, self.source.pk)
        self.target.refresh_from_db()
        self.assertEqual(self.target.arrivalKg, Decimal("500.50"))

    def test_worker_feed_has_no_merge_form(self):
        self.client.force_login(self.worker)
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertNotContains(resp, 'action="/resin-lots/merge/"')
        self.assertContains(resp, "仅主管")

    def test_staff_feed_shows_merge_form_with_origin_rule_hint(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, 'action="/resin-lots/merge/"')
        self.assertContains(resp, "以目标批来源地为准")

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(
            reverse("resin_lot_merge"),
            {"source": self.source.pk, "target": self.target.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])
        self.assertTrue(ResinLot.objects.filter(pk=self.source.pk).exists())


class MergeDrawingRejectionTests(MergeBase):
    def assert_no_side_effects(self):
        self.assertTrue(ResinLot.objects.filter(pk=self.source.pk).exists())
        self.assertTrue(ResinLot.objects.filter(pk=self.target.pk).exists())
        for run in (self.run_open1, self.run_open2, self.run_closed):
            run.refresh_from_db()
            self.assertEqual(run.resinLot_id, self.source.pk)
        self.target.refresh_from_db()
        self.assertEqual(self.target.arrivalKg, Decimal("500.50"))

    def test_source_open_run_on_drawing_hearth_rejects(self):
        self.h_src1.phase = FireHearth.PHASE_DRAWING
        self.h_src1.save(update_fields=["phase"])
        self.client.force_login(self.admin)
        resp = self.post_merge(follow=True)
        self.assertContains(resp, "合并已拒绝")
        self.assert_no_side_effects()

    def test_target_open_run_on_drawing_hearth_rejects(self):
        self.h_tgt.phase = FireHearth.PHASE_DRAWING
        self.h_tgt.save(update_fields=["phase"])
        self.client.force_login(self.admin)
        resp = self.post_merge(follow=True)
        self.assertContains(resp, "合并已拒绝")
        self.assert_no_side_effects()

    def test_closed_run_on_drawing_hearth_does_not_block(self):
        # 已收灶历史挂在出胶灶上不算「未收灶值守」，不阻断合并
        self.h_src3.phase = FireHearth.PHASE_DRAWING
        self.h_src3.save(update_fields=["phase"])
        self.client.force_login(self.admin)
        self.post_merge()
        self.assertFalse(ResinLot.objects.filter(pk=self.source.pk).exists())

    def test_same_lot_rejected(self):
        self.client.force_login(self.admin)
        resp = self.post_merge(source=self.source, target=self.source, follow=True)
        self.assertContains(resp, "不能是同一批")
        self.assert_no_side_effects()


class MergeServiceTests(MergeBase):
    def test_service_rejects_drawing_open_run(self):
        self.h_tgt.phase = FireHearth.PHASE_DRAWING
        self.h_tgt.save(update_fields=["phase"])
        with self.assertRaises(ValidationError):
            merge_resin_lots(self.source, self.target)
        self.assert_no_side_effects_service()

    def test_service_rejects_same_lot(self):
        with self.assertRaises(ValidationError):
            merge_resin_lots(self.source, self.source)

    def assert_no_side_effects_service(self):
        self.assertTrue(ResinLot.objects.filter(pk=self.source.pk).exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.arrivalKg, Decimal("500.50"))


class SeedDataTests(TestCase):
    def test_seed_has_two_lots_with_open_runs(self):
        from apps.kiln.seed import ensure_seed_data

        ensure_seed_data()
        lots_with_open = [
            lot
            for lot in ResinLot.objects.all()
            if lot.runs.filter(closedAt__isnull=True).exists()
        ]
        self.assertGreaterEqual(len(lots_with_open), 2)
