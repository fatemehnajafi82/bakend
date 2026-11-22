from django.urls import path

from .live import DeviceIngestView, DeviceView, LiveView, PhoneIngestView, RecordStartView, RecordStopView
from .memories import MemoryDetailView, MemoryFileView, MemoryListCreateView, MemorySummaryView
from .album import AlbumListView, AlbumWeeksView
from .views import RecordingAudioView, RecordingDetailView, RecordingListCreateView

urlpatterns = [
    path("recordings/", RecordingListCreateView.as_view(), name="recording-list"),
    path("recordings/<int:pk>/", RecordingDetailView.as_view(), name="recording-detail"),
    path("recordings/<int:pk>/audio/", RecordingAudioView.as_view(), name="recording-audio"),

    # آلبوم
    path("album/", AlbumListView.as_view(), name="album-list"),
    path("album/weeks/", AlbumWeeksView.as_view(), name="album-weeks"),

    path("memories/", MemoryListCreateView.as_view(), name="memory-list"),
    path("memories/summary/", MemorySummaryView.as_view(), name="memory-summary"),
    path("memories/<int:pk>/", MemoryDetailView.as_view(), name="memory-detail"),
    path("memories/<int:pk>/file/", MemoryFileView.as_view(), name="memory-file"),

    # دستگاه و پخش زنده
    path("device/chunks/", DeviceIngestView.as_view()),
    path("device/", DeviceView.as_view()),             # ثبت/وضعیت/جدا کردن دستگاه
    path("live/ingest/", PhoneIngestView.as_view()),   # گوشی با JWT
    path("live/", LiveView.as_view()),
    path("live/record/start/", RecordStartView.as_view()),
    path("live/record/stop/", RecordStopView.as_view()),
]