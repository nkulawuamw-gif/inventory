import json
import string
import random
from django.db.models import Q
from django.contrib.auth import get_user_model
User = get_user_model()
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.clickjacking import xframe_options_exempt
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from datetime import timedelta
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import UserPresence, Message, Call, Meeting, MeetingParticipant, UserProfile
from .middleware import get_user_profile, shop_access_required


@csrf_exempt
def heartbeat(request):
    if request.user.is_authenticated:
        UserPresence.objects.update_or_create(
            user=request.user,
            defaults={'is_online': True, 'last_seen': timezone.now()}
        )
    return JsonResponse({'status': 'ok'})


@csrf_exempt
def mark_offline(request):
    if request.user.is_authenticated:
        UserPresence.objects.filter(user=request.user).update(is_online=False)
    return JsonResponse({'status': 'ok'})


@login_required
@login_required
def get_all_users(request):
    users = User.objects.all().values('id', 'username')
    return JsonResponse({'users': list(users)})


@login_required
def get_online_users(request):
    threshold = timezone.now() - timedelta(seconds=30)
    online_users = UserPresence.objects.filter(
        is_online=True, last_seen__gte=threshold
    ).select_related('user__user_profile__assigned_shop')

    users = []
    for presence in online_users:
        user = presence.user
        shop_name = ''
        is_admin = user.is_superuser
        if user.is_superuser:
            shop_name = 'Admin'
        else:
            try:
                profile = user.user_profile
                if profile.assigned_shop:
                    shop_name = profile.assigned_shop.name
                if profile.is_admin:
                    is_admin = True
            except UserProfile.DoesNotExist:
                pass

        display_name = user.get_full_name().strip() or user.username
        users.append({
            'id': user.id,
            'username': user.username,
            'display_name': display_name,
            'shop': shop_name,
            'is_admin': is_admin,
        })

    return JsonResponse({'users': users})



@login_required
def calls_view(request):
    calls = Call.objects.filter(
        Q(caller=request.user) | Q(receiver=request.user)
    ).select_related('caller', 'receiver').order_by('-timestamp')[:50]

    incoming_ringing = Call.objects.filter(receiver=request.user, status='ringing')

    return render(request, 'stock_manager/calls.html', {
        'calls': calls,
        'incoming_ringing': incoming_ringing,
        'page_title': 'Calls',
    })


@login_required
@csrf_exempt
def initiate_call(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({'status': 'error', 'error': 'Invalid JSON body'}, status=400)

        receiver_username = data.get('callee_username', '').strip()
        call_type = data.get('call_type', 'audio')

        if receiver_username:
            try:
                receiver = User.objects.get(username=receiver_username)
            except User.DoesNotExist:
                return JsonResponse({'status': 'error', 'error': 'User not found'}, status=404)

            try:
                call = Call.objects.create(
                    caller=request.user,
                    receiver=receiver,
                    call_type=call_type,
                )
            except Exception:
                return JsonResponse({'status': 'error', 'error': 'Failed to create call'}, status=500)

            try:
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f'user_{receiver.id}',
                    {
                        'type': 'incoming_call',
                        'call_id': call.id,
                        'caller': request.user.get_full_name() or request.user.username,
                        'caller_id': request.user.id,
                        'call_type': call_type,
                        'room_name': call.room_name,
                    }
                )
            except Exception:
                pass

            return JsonResponse({
                'status': 'initiated',
                'call_id': call.id,
                'room_name': call.room_name,
            })

    return JsonResponse({'status': 'error', 'error': 'Invalid request'}, status=400)


@login_required
@xframe_options_exempt
def call_room(request, call_id):
    call = get_object_or_404(Call, id=call_id)

    if call.caller != request.user and call.receiver != request.user:
        return redirect('calls')

    if call.status == 'ended':
        messages.info(request, 'This call has already ended.')
        return redirect('calls')

    if call.status == 'ringing' and call.receiver == request.user:
        call.status = 'accepted'
        call.save()

    other_user = call.receiver if call.caller == request.user else call.caller
    room_name = call.room_name

    return render(request, 'stock_manager/jitsi_room.html', {
        'call': call,
        'other_user': other_user,
        'room_name': room_name,
        'is_initiator': request.user == call.caller,
        'page_title': 'Call - Pure WebRTC',
    })


@login_required
@csrf_exempt
def end_call(request, call_id):
    call = get_object_or_404(Call, id=call_id)
    if call.caller == request.user or call.receiver == request.user:
        call.status = 'ended'
        call.save()

        other_id = call.receiver.id if call.caller == request.user else call.caller.id
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'user_{other_id}',
                {
                    'type': 'incoming_call',
                    'call_id': call.id,
                    'caller': 'CALL_ENDED',
                    'caller_id': 0,
                    'call_type': call.call_type,
                }
            )
            # Also notify the call-signaling group so the other peer in the room gets the end signal
            async_to_sync(channel_layer.group_send)(
                f'call_signal_{call.id}',
                {
                    'type': 'call_ended',
                    'call_id': call.id,
                }
            )
        except Exception:
            pass

    if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.content_type == 'application/json'
            or request.content_type == 'application/x-www-form-urlencoded'
            or request.method == 'POST'):
        return JsonResponse({'status': 'ended'})
    return redirect('calls')


@shop_access_required
def meetings_view(request):
    meetings = Meeting.objects.filter(is_active=True).select_related('host').order_by('-started_at')
    past_meetings = Meeting.objects.filter(is_active=False).select_related('host').order_by('-started_at')[:10]

    return render(request, 'stock_manager/meetings.html', {
        'active_meetings': meetings,
        'past_meetings': past_meetings,
        'page_title': 'Meetings',
    })


@shop_access_required
def create_meeting(request):
    if request.method == 'POST':
        name = request.POST.get('name', 'Meeting').strip()
        meeting_type = request.POST.get('meeting_type', 'video')
        description = request.POST.get('description', '').strip()

        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

        meeting = Meeting.objects.create(
            host=request.user,
            name=name,
            meeting_code=code,
            meeting_type=meeting_type,
            description=description,
        )

        MeetingParticipant.objects.create(meeting=meeting, user=request.user)

        messages.success(request, f'Meeting "{name}" created. Code: {code}')
        return redirect('meeting_room', meeting_code=code)

    return redirect('meetings')


@shop_access_required
def join_meeting(request, meeting_code):
    meeting = get_object_or_404(Meeting, meeting_code=meeting_code, is_active=True)

    MeetingParticipant.objects.get_or_create(meeting=meeting, user=request.user)

    messages.success(request, f'Joined meeting "{meeting.name}"')
    return redirect('meeting_room', meeting_code=meeting_code)


@shop_access_required
def meeting_room(request, meeting_code):
    meeting = get_object_or_404(Meeting, meeting_code=meeting_code)
    participants = MeetingParticipant.objects.filter(meeting=meeting).select_related('user')

    return render(request, 'stock_manager/meeting_room.html', {
        'meeting': meeting,
        'participants': participants,
        'page_title': f'Meeting: {meeting.name}',
    })


@shop_access_required
def leave_meeting(request, meeting_code):
    meeting = get_object_or_404(Meeting, meeting_code=meeting_code)
    participant = MeetingParticipant.objects.filter(meeting=meeting, user=request.user).first()

    if participant:
        participant.left_at = timezone.now()
        participant.save()

    if meeting.host == request.user:
        meeting.is_active = False
        meeting.ended_at = timezone.now()
        meeting.save()
        MeetingParticipant.objects.filter(meeting=meeting, left_at__isnull=True).update(left_at=timezone.now())
        messages.success(request, f'Meeting "{meeting.name}" ended.')
    else:
        messages.success(request, f'Left meeting "{meeting.name}".')

    return redirect('meetings')


@shop_access_required
def meeting_signal(request, meeting_code):
    meeting = get_object_or_404(Meeting, meeting_code=meeting_code)

    if request.method == 'POST':
        data = json.loads(request.body)
        data['from_user'] = request.user.id
        data['timestamp'] = timezone.now().isoformat()

        signal_key = f'signal_{data.get("type", "unknown")}_{request.user.id}'
        current_data = meeting.signaling_data if hasattr(meeting, 'signaling_data') and meeting.signaling_data else {}
        if isinstance(current_data, dict):
            current_data[signal_key] = data
        else:
            current_data = {signal_key: data}

        Meeting.objects.filter(id=meeting.id).update(signaling_data=current_data)

        return JsonResponse({'status': 'ok'})

    data = getattr(meeting, 'signaling_data', {}) or {}
    filtered = {k: v for k, v in data.items() if v.get('from_user') != request.user.id}
    return JsonResponse({'signals': filtered})


# ---------------- CHAT ----------------

@login_required
def chat_view(request):
    conversations = {}
    messages = Message.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user)
    ).select_related('sender', 'receiver').order_by('-timestamp')

    for msg in messages:
        other = msg.receiver if msg.sender == request.user else msg.sender
        if other.id not in conversations:
            conversations[other.id] = {
                'user': other,
                'last_message': msg.content,
                'timestamp': msg.timestamp,
                'unread': 0,
            }
        if msg.receiver == request.user and not msg.is_read:
            conversations[other.id]['unread'] += 1

    total_unread = sum(c['unread'] for c in conversations.values())

    return render(request, 'stock_manager/chat.html', {
        'conversations': sorted(conversations.values(), key=lambda c: c['timestamp'], reverse=True),
        'total_unread': total_unread,
        'page_title': 'Chat',
    })


@login_required
def unread_count(request):
    count = Message.objects.filter(receiver=request.user, is_read=False).count()
    return JsonResponse({'count': count})


@login_required
def mark_read(request):
    if request.method == 'POST':
        sender_id = request.POST.get('sender_id')
        if sender_id:
            Message.objects.filter(
                sender_id=sender_id, receiver=request.user, is_read=False
            ).update(is_read=True)
    return JsonResponse({'status': 'ok'})


@login_required
def get_conversation(request, user_id):
    other = get_object_or_404(User, id=user_id)
    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other) | Q(sender=other, receiver=request.user)
    ).select_related('sender').order_by('timestamp')

    Message.objects.filter(sender=other, receiver=request.user, is_read=False).update(is_read=True)

    return JsonResponse({
        'messages': [{
            'id': m.id,
            'content': m.content,
            'sender': m.sender.username,
            'timestamp': m.timestamp.isoformat(),
            'is_mine': m.sender == request.user,
        } for m in msgs]
    })


# ---------------- CALLS ----------------

@login_required
def check_incoming_call(request):
    try:
        ringing = Call.objects.filter(receiver=request.user, status='ringing').select_related('caller').first()
        if ringing:
            return JsonResponse({
                'ringing': True,
                'call_id': ringing.id,
                'caller': ringing.caller.get_full_name() or ringing.caller.username,
                'call_type': ringing.call_type,
                'room_name': ringing.room_name,
            })
        return JsonResponse({'ringing': False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def incoming_calls(request):
    try:
        calls = list(Call.objects.filter(receiver=request.user).values())
        return JsonResponse({"calls": calls})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def delete_call(request, call_id):
    call = get_object_or_404(Call, id=call_id)
    if request.method != 'POST':
        return redirect('calls')
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin
    if call.caller != request.user and call.receiver != request.user and not user_is_admin:
        messages.error(request, 'Not authorised to delete this call.')
        return redirect('calls')
    call.delete()
    messages.success(request, 'Call deleted.')
    return redirect('calls')


@login_required
def clear_calls(request):
    if request.method != 'POST':
        return redirect('calls')
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin
    if user_is_admin:
        Call.objects.all().delete()
        messages.success(request, 'All call logs cleared.')
    else:
        Call.objects.filter(Q(caller=request.user) | Q(receiver=request.user)).delete()
        messages.success(request, 'Your call logs cleared.')
    return redirect('calls')

