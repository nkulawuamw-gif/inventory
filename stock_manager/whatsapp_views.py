from django.shortcuts import redirect
from django.http import JsonResponse
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from .middleware import shop_access_required


@shop_access_required
def whatsapp_inbox(request):
    messages.error(request, 'WhatsApp features are not available.')
    return redirect('dashboard')


@csrf_exempt
def whatsapp_webhook(request):
    return JsonResponse({'status': 'unavailable'})
