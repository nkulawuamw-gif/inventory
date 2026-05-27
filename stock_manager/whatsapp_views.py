from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from .middleware import shop_access_required


def whatsapp_inbox(request):
    """WhatsApp messaging page - placeholder for future integration."""
    return render(request, 'stock_manager/whatsapp_inbox.html')


@csrf_exempt
def whatsapp_webhook(request):
    return JsonResponse({'status': 'unavailable'})
