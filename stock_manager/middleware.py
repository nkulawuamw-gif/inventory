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
SHOP_RESTRICTED_VIEWS = ['shop_dashboard', 'point_of_sale', 'print_receipt']


def get_user_profile(user):
    if user.is_authenticated:
        if user.is_superuser:
            return None
        try:
            return user.profile
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


def shop_access_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        resolver_match = request.resolver_match
        url_name = resolver_match.url_name if resolver_match else None

        # 🔐 Not logged in
        if not request.user.is_authenticated:
            return redirect('admin:login')

        # 👑 Superuser bypass
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        profile = get_user_profile(request.user)

        # ❌ No profile
        if profile is None:
            messages.error(request, 'Your account is not configured. Please contact the administrator.')
            return redirect('admin:index')

        # 👑 Admin users
        if profile.is_admin:
            return view_func(request, *args, **kwargs)

        # ⚠️ ONLY restrict specific admin-only views
        if url_name in ADMIN_ONLY_VIEWS:
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('shop_dashboard', shop_slug=profile.assigned_shop.name.replace(' ', '-').lower())

        # 🏪 Shop restriction
        if url_name in SHOP_RESTRICTED_VIEWS:
            shop_slug = kwargs.get('shop_slug', '')

            if profile.assigned_shop:
                allowed_slug = profile.assigned_shop.name.replace(' ', '-').lower()

                if shop_slug != allowed_slug:
                    messages.error(request, f'You can only access the {profile.assigned_shop.name} dashboard.')
                    return redirect('shop_dashboard', shop_slug=allowed_slug)
            else:
                messages.error(request, 'No shop assigned to your account.')
                return redirect('admin:index')

        # ✅ ALWAYS return response
        return view_func(request, *args, **kwargs)

    return _wrapped_view