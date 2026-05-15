import json
import hashlib
import hmac
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import Q, Count, Max
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .models import WhatsAppSetting, WhatsAppMessage
from .middleware import get_user_profile, shop_access_required


@shop_access_required
def whatsapp_inbox(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin
    if not user_is_admin:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    conversations = (
        WhatsAppMessage.objects
        .values('customer_number', 'customer_name')
        .annotate(
            last_time=Max('created_at'),
            unread=Count('id', filter=Q(is_read=False, is_from_customer=True)),
        )
        .order_by('-last_time')
    )

    selected_number = request.GET.get('customer')
    wa_messages = []
    selected_customer = None
    if selected_number:
        selected_customer = selected_number
        wa_messages = WhatsAppMessage.objects.filter(customer_number=selected_number).order_by('created_at')
        wa_messages.filter(is_from_customer=True, is_read=False).update(is_read=True)

    context = {
        'conversations': conversations,
        'wa_messages': wa_messages,
        'selected_customer': selected_customer,
        'page_title': 'WhatsApp Messages',
        'whatsapp': WhatsAppSetting.get_profile(),
    }
    return render(request, 'stock_manager/whatsapp_inbox.html', context)


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == 'GET':
        challenge = request.GET.get('hub.challenge')
        verify_token = request.GET.get('hub.verify_token')
        setting = WhatsAppSetting.get_profile()
        if verify_token == setting.webhook_secret and challenge:
            return HttpResponse(challenge, content_type='text/plain')
        return HttpResponse('Verification failed', status=403)

    if request.method == 'POST':
        setting = WhatsAppSetting.get_profile()
        if not setting.is_active:
            return HttpResponse('Webhook inactive', status=200)

        signature = request.META.get('HTTP_X_HUB_SIGNATURE_256', '')
        if setting.webhook_secret and setting.api_key:
            expected = hmac.new(
                setting.webhook_secret.encode(),
                request.body,
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(f'sha256={expected}', signature):
                return HttpResponse('Invalid signature', status=403)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse('Invalid JSON', status=400)

        entries = data.get('entry', [])
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                messages_list = value.get('messages', [])
                for msg in messages_list:
                    customer_number = msg.get('from', '')
                    msg_body = ''
                    text = msg.get('text', {})
                    if text:
                        msg_body = text.get('body', '')
                    if not msg_body:
                        for entry2 in msg.get('interactive', {}).values():
                            if isinstance(entry2, dict):
                                msg_body = entry2.get('body', {}).get('text', '')

                    wa_msg_id = msg.get('id', '')
                    if wa_msg_id and WhatsAppMessage.objects.filter(wa_message_id=wa_msg_id).exists():
                        continue

                    contact_name = customer_number
                    contacts = value.get('contacts', [])
                    for c in contacts:
                        if c.get('wa_id') == customer_number:
                            profile_name = c.get('profile', {}).get('name', '')
                            contact_name = profile_name or customer_number

                    WhatsAppMessage.objects.create(
                        customer_number=customer_number,
                        customer_name=contact_name,
                        wa_message_id=wa_msg_id,
                        body=msg_body,
                        is_from_customer=True,
                    )

                    if setting.greeting_message:
                        prev = WhatsAppMessage.objects.filter(
                            customer_number=customer_number,
                            is_from_customer=True
                        ).count()
                        if prev <= 1:
                            WhatsAppMessage.objects.create(
                                customer_number=customer_number,
                                customer_name=contact_name,
                                body=setting.greeting_message,
                                is_from_customer=False,
                            )

        return JsonResponse({'status': 'ok'})

    return HttpResponse('Method not allowed', status=405)
