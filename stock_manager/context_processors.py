from .models import Shop, UserProfile


# =========================
# SAFE USER ACCESS
# =========================

def get_user_access(user):
    if not user.is_authenticated:
        return {
            'is_admin': False,
            'assigned_shop': None,
            'permissions': [],
        }

    if user.is_superuser:
        return {
            'is_admin': True,
            'assigned_shop': None,
            'permissions': [],
        }

    profile, _ = UserProfile.objects.get_or_create(user=user)

    return {
        'is_admin': profile.is_admin,
        'assigned_shop': profile.assigned_shop,
        'permissions': profile.permissions or [],
    }


# =========================
# GLOBAL CONTEXT
# =========================

def global_context(request):
    user_access = get_user_access(request.user)

    # SAFE SHOP QUERY
    shops = Shop.objects.all()

    # ONLY FILTER FOR NON-ADMINS WITH ASSIGNED SHOP
    if (
        request.user.is_authenticated
        and not user_access['is_admin']
        and user_access['assigned_shop']
    ):
        shops = Shop.objects.filter(pk=user_access['assigned_shop'].pk)

    # SAFE RESOLVER ACCESS
    resolver = getattr(request, "resolver_match", None)
    shop_slug = None

    if resolver:
        shop_slug = getattr(resolver, "kwargs", {}).get("shop_slug", "")

    return {
        "all_shops": shops,
        "user_access": user_access,
        "shop_slug": shop_slug,
    }
