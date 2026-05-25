from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from .models import UserProfile


EXEMPT_PATHS = [
    '/admin/',   # allow full admin
    '/heartbeat/',
    '/mark-offline/',
    '/whatsapp/webhook/',
]

ADMIN_ONLY_VIEWS = ['admin_manage', 'financial_report', 'dashboard_bulk_import', 'export_csv']
SHOP_RESTRICTED_VIEWS = ['shop_dashboard', 'point_of_sale']

PERMISSION_GATED_VIEWS = [
    'dashboard', 'admin_manage', 'financial_report', 'sales_history',
    'whatsapp',
    'settings', 'export_csv', 'dashboard_bulk_import', 'inventory',
]

ACCESS_DENIED = {
    'dashboard': 'You do not have access to the main dashboard.',
    'admin_manage': 'You do not have permission to manage inventory.',
    'financial_report': 'You do not have access to the financial report.',
    'sales_history': 'You do not have access to sales history.',
    'export_csv': 'You do not have access to export.',
    'settings': 'You do not have access to settings.',
    'dashboard_bulk_import': 'You do not have access to bulk import.',
}


def get_user_profile(user):
    if user.is_authenticated:
        if user.is_superuser:
            return None
        try:
            return user.user_profile
        except UserProfile.DoesNotExist:
            return None
    return None


class ShopAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        for path in EXEMPT_PATHS:
            if request.path.startswith(path):
                return self.get_response(request)

        user = request.user
        profile = get_user_profile(user)

        if user.is_authenticated and not user.is_superuser and profile is None:
            messages.error(request, 'Your account is not configured. Please contact the administrator.')
            return redirect('admin:index')

        response = self.get_response(request)
        return response


def go_back(request, fallback='dashboard'):
    referer = request.META.get('HTTP_REFERER', '')
    if referer and referer.startswith(request.build_absolute_uri('/')):
        return redirect(referer)
    return redirect(fallback)

def shop_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        resolver_match = request.resolver_match
        url_name = resolver_match.url_name if resolver_match else None

        if not request.user.is_authenticated:
            return go_back(request, 'landing')

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        profile = get_user_profile(request.user)

        if profile is None:
            messages.error(request, 'Your account is not configured.')
            return go_back(request, 'landing')

        if profile.is_admin:
            return view_func(request, *args, **kwargs)

        if url_name in ACCESS_DENIED:
            if profile.has_permission(url_name):
                return view_func(request, *args, **kwargs)
            msg = ACCESS_DENIED.get(url_name, 'No access.')
            messages.error(request, msg)
            return go_back(request, 'dashboard')

        if url_name in SHOP_RESTRICTED_VIEWS:
            shop_slug = kwargs.get('shop_slug', '')

            if profile.assigned_shop:
                allowed_slug = profile.assigned_shop.slug
                if shop_slug != allowed_slug:
                    messages.error(request, 'Access denied to this shop.')
                    return redirect('shop_dashboard', shop_slug=allowed_slug)
            else:
                return go_back(request, 'dashboard')

        if profile.permissions is not None and url_name in PERMISSION_GATED_VIEWS and url_name not in profile.permissions:
            messages.error(request, 'Access denied.')
            return go_back(request, 'dashboard')

        return view_func(request, *args, **kwargs)

    return _wrapped_view