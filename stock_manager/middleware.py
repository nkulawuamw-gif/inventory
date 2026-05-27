from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from .models import UserProfile


# =========================
# EXEMPT PATHS (SAFE MATCH)
# =========================

EXEMPT_PATHS = (
    '/admin/',
    '/heartbeat/',
    '/mark-offline/',
    '/whatsapp/webhook/',
)


def is_exempt_path(path):
    return any(path.startswith(p) for p in EXEMPT_PATHS)


# =========================
# SAFE PROFILE GETTER
# =========================

def get_user_profile(user):
    if not user.is_authenticated or user.is_superuser:
        return None

    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


# =========================
# MIDDLEWARE
# =========================

class ShopAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        if is_exempt_path(request.path):
            return self.get_response(request)

        # ensure profile exists early (prevents 500s in views)
        if request.user.is_authenticated and not request.user.is_superuser:
            UserProfile.objects.get_or_create(user=request.user)

        return self.get_response(request)


# =========================
# SAFE BACK NAV
# =========================

def go_back(request, fallback='dashboard'):
    referer = request.META.get('HTTP_REFERER')

    if referer and referer.startswith(request.build_absolute_uri('/')):
        return redirect(referer)

    return redirect(fallback)


# =========================
# ACCESS DECORATOR
# =========================

def shop_access_required(view_func):

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):

        url_name = getattr(request.resolver_match, 'url_name', None)

        if not request.user.is_authenticated:
            return redirect('landing')

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        profile = get_user_profile(request.user)

        if not profile:
            messages.error(request, "Account not configured.")
            return redirect('landing')

        # ADMIN FULL ACCESS
        if profile.is_admin:
            return view_func(request, *args, **kwargs)

        # SHOP RESTRICTION SAFETY
        shop_slug = kwargs.get('shop_slug')

        if profile.assigned_shop and shop_slug:
            if profile.assigned_shop.slug != shop_slug:
                messages.error(request, "Access denied to this shop.")
                return redirect('shop_dashboard', shop_slug=profile.assigned_shop.slug)

        # PERMISSION CHECK (SAFE)
        if profile.permissions and url_name:
            if url_name in profile.permissions:
                return view_func(request, *args, **kwargs)

            messages.error(request, "Access denied.")
            return go_back(request, 'dashboard')

        return view_func(request, *args, **kwargs)

    return wrapper