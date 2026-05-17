from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from .models import UserProfile


ACCESS_DENIED = {
    'dashboard': 'You do not have access to the main dashboard.',
    'admin_manage': 'You do not have permission to manage inventory.',
    'financial_report': 'You do not have access to the financial report.',
    'sales_history': 'You do not have access to sales history.',
    'search': 'You do not have access to search.',
    'export_csv': 'You do not have access to export.',
    'settings': 'You do not have access to settings.',
    'download_template': 'You do not have access to download templates.',
    'dashboard_bulk_import': 'You do not have access to bulk import.',
    'chat': 'You do not have access to chat.',
    'calls': 'You do not have access to calls.',
    'meetings': 'You do not have access to meetings.',
    'send_message': 'You do not have access to messaging.',
    'get_messages': 'You do not have access to messaging.',
    'unread_count': 'You do not have access to messaging.',
    'initiate_call': 'You do not have access to calls.',
    'call_room': 'You do not have access to calls.',
    'end_call': 'You do not have access to calls.',
    'call_signal': 'You do not have access to calls.',
    'create_meeting': 'You do not have access to meetings.',
    'join_meeting': 'You do not have access to meetings.',
    'meeting_room': 'You do not have access to meetings.',
    'leave_meeting': 'You do not have access to meetings.',
    'meeting_signal': 'You do not have access to meetings.',
    'whatsapp_inbox': 'You do not have access to WhatsApp messages.',
}

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


EXEMPT_PATHS = ['/heartbeat/', '/mark-offline/', '/admin/login/', '/admin/logout/', '/whatsapp/webhook/']


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
            if request.path != reverse('admin:index'):
                messages.error(request, 'Your account is not configured. Please contact the administrator.')
                return redirect('admin:index')

        response = self.get_response(request)
        return response


def shop_access_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        url_name = request.resolver_match.url_name

        if not request.user.is_authenticated:
            return redirect('admin:login')

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        profile = get_user_profile(request.user)

        if profile is None:
            messages.error(request, 'Your account is not configured. Please contact the administrator.')
            return redirect('admin:index')

        if profile.is_admin:
            return view_func(request, *args, **kwargs)

        if url_name in ACCESS_DENIED and not profile.is_admin:
            msg = ACCESS_DENIED.get(url_name, 'You do not have access to this page.')
            messages.error(request, msg)
            return redirect('shop_dashboard', shop_slug=profile.assigned_shop.name.replace(' ', '-').lower())

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

        return view_func(request, *args, **kwargs)

    return _wrapped_view
