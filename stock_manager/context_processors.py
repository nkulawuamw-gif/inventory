from .models import Shop, UserProfile, CompanyProfile, WhatsAppSetting


def get_user_access(user):
    if user.is_superuser:
        return {'is_admin': True, 'assigned_shop': None}
    try:
        profile = user.profile
        return {'is_admin': profile.is_admin, 'assigned_shop': profile.assigned_shop}
    except UserProfile.DoesNotExist:
        return {'is_admin': True, 'assigned_shop': None}


def global_context(request):
    user_access = get_user_access(request.user) if request.user.is_authenticated else {'is_admin': False, 'assigned_shop': None}
    shops = Shop.objects.all()
    if request.user.is_authenticated and not user_access['is_admin'] and user_access['assigned_shop']:
        shops = Shop.objects.filter(id=user_access['assigned_shop'].id)

    return {
        'all_shops': shops,
        'user_access': user_access,
        'shop_slug': getattr(request, 'resolver_match', None) and getattr(request.resolver_match, 'kwargs', {}).get('shop_slug', ''),
        'company': CompanyProfile.get_profile(),
        'whatsapp': WhatsAppSetting.get_profile(),
    }
