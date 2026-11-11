from django.urls import path

from .live import DeviceIngestView, LiveView, RecordStartView, RecordStopView
from .views import RecordingAudioView, RecordingDetailView, RecordingListCreateView

urlpatterns = [
    path("recordings/", RecordingListCreateView.as_view(), name="recording-list"),
    path("recordings/<int:pk>/", RecordingDetailView.as_view(), name="recording-detail"),
    path("recordings/<int:pk>/audio/", RecordingAudioView.as_view(), name="recording-audio"),

    # دستگاه و پخش زنده
    path("device/chunks/", DeviceIngestView.as_view()),
    path("live/", LiveView.as_view()),
    path("live/record/start/", RecordStartView.as_view()),
    path("live/record/stop/", RecordStopView.as_view()),
]