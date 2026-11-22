"""آلبوم یکپارچه: ضبط‌های صوتی (Recording) و خاطره‌ها (Memory) در یک فهرست."""
from django.db.models import CharField, Count, F, Max, Q, Sum, Value
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .memories import MemorySerializer
from .models import Memory, Recording
from .serializers import RecordingSerializer
from .views import RecordingPagination, apply_album_filters

AUDIO = "audio"                                    # ضبط صدای داپلر (مدل Recording)
KINDS = (AUDIO, Memory.PHOTO, Memory.VIDEO, Memory.MUSIC)


class AlbumListView(APIView):
    """GET /api/album/ — همهٔ خاطره‌ها (صدا، عکس، ویدئو، موسیقی) از جدید به قدیم.

    فیلترها: ?kind=audio|photo|video|music · ?week= · ?from= · ?to= · ?q= · ?favorite=true
    صفحه‌بندی: ?limit= و ?offset=
    هر آیتم فیلد kind دارد؛ برای ویرایش/حذف: audio → /api/recordings/<id>/ و بقیه → /api/memories/<id>/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        kind = params.get("kind", "")
        if kind and kind not in KINDS:
            raise ValidationError({"kind": "نوع باید audio یا photo یا video یا music باشد."})

        parts = []
        if kind in ("", AUDIO):
            rec = apply_album_filters(Recording.objects.filter(user=request.user), params)
            parts.append(rec.order_by().annotate(kind=Value(AUDIO, output_field=CharField()))
                         .values("id", "created_at", "kind"))
        if kind != AUDIO:
            mem = apply_album_filters(Memory.objects.filter(user=request.user), params)
            if kind:
                mem = mem.filter(kind=kind)
            parts.append(mem.order_by().values("id", "created_at", "kind"))

        rows = parts[0].union(*parts[1:], all=True) if len(parts) > 1 else parts[0]
        rows = rows.order_by("-created_at", "kind", "-id")

        paginator = RecordingPagination()
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response(self._hydrate(page, request))

    @staticmethod
    def _hydrate(page, request):
        ctx = {"request": request}
        ids = {AUDIO: [], Memory.PHOTO: [], Memory.VIDEO: [], Memory.MUSIC: []}
        for row in page:
            ids[row["kind"]].append(row["id"])

        recs = {r.pk: r for r in Recording.objects.filter(pk__in=ids[AUDIO])}
        mems = {m.pk: m for m in Memory.objects.filter(pk__in=sum((ids[k] for k in KINDS[1:]), []))}

        items = []
        for row in page:
            if row["kind"] == AUDIO:
                data = dict(RecordingSerializer(recs[row["id"]], context=ctx).data)
                data["kind"] = AUDIO
                data["url"] = data["audio_url"]
                detail = reverse("recording-detail", args=[row["id"]])
            else:
                data = dict(MemorySerializer(mems[row["id"]], context=ctx).data)
                data["url"] = data["file_url"]
                detail = reverse("memory-detail", args=[row["id"]])
            data["detail_url"] = request.build_absolute_uri(detail)
            items.append(data)
        return items


class AlbumWeeksView(APIView):
    """خلاصهٔ آلبوم به تفکیک هفتهٔ بارداری (جدیدترین هفته اول).

    برای هر هفته: تعداد کل خاطره‌ها، تعداد هر نوع (kinds)، تعداد علاقه‌مندی، مجموع مدت (ثانیه)
    و زمان آخرین خاطره. موارد بدون هفته (قبل از ثبت تاریخ زایمان) در آیتم آخر با week=null می‌آیند.
    """
    permission_classes = [IsAuthenticated]

    AGG = dict(
        count=Count("id"),
        favorites=Count("id", filter=Q(is_favorite=True)),
        total_duration=Sum("duration"),
        last_recorded_at=Max("created_at"),
    )

    def get(self, request):
        weeks = {}

        def slot(week):
            return weeks.setdefault(week, {
                "week": week, "count": 0, "kinds": {k: 0 for k in KINDS},
                "favorites": 0, "total_duration": 0, "last_recorded_at": None,
            })

        def add(week, kind, n, favs, duration, last):
            w = slot(week)
            w["count"] += n
            w["kinds"][kind] += n
            w["favorites"] += favs
            w["total_duration"] += duration or 0
            if last and (w["last_recorded_at"] is None or last > w["last_recorded_at"]):
                w["last_recorded_at"] = last

        for r in (Recording.objects.filter(user=request.user)
                  .values("pregnancy_week").annotate(**self.AGG).order_by()):
            add(r["pregnancy_week"], AUDIO, r["count"], r["favorites"], r["total_duration"], r["last_recorded_at"])
        for r in (Memory.objects.filter(user=request.user)
                  .values("pregnancy_week", "kind").annotate(**self.AGG).order_by()):
            add(r["pregnancy_week"], r["kind"], r["count"], r["favorites"], r["total_duration"], r["last_recorded_at"])

        ordered = sorted(weeks.values(), key=lambda w: (w["week"] is None, -(w["week"] or 0)))
        return Response({"total": sum(w["count"] for w in ordered), "weeks": ordered})
