from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.http import JsonResponse
from django.views.generic.base import RedirectView

def health_check(request):
    return JsonResponse({'status': 'ok'})

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('favicon.ico', RedirectView.as_view(url='/static/favicon.ico', permanent=True)),
    path('admin/data-migration/', include('data_migration.urls')),
    path('admin/system-updates/', include('system_updates.urls')),
    path('admin/', admin.site.urls),
    path('', include('stock_manager.urls')),
    path('audit/', include('audit.urls')),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]

urlpatterns += [
    re_path(r'^static/(?P<path>.*)$', staticfiles_serve, {'insecure': True}),
]
