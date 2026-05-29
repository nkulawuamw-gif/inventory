from django.urls import path
from . import views

urlpatterns = [
    path('', views.system_updates_view, name='system_updates'),
]
