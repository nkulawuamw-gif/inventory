import json
import string
import random
from django.db.models import Q, Max
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from datetime import timedelta, timezone as dt_timezone
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
def chat_view(request):
    threshold = timezone.now() - timedelta(seconds=60)
    online_ids = set(UserPresence.objects.filter(
        is_online=True, last_seen__gte=threshold
    ).values_list('user_id', flat=True))

    contacts = {}

    conversations = Message.objects.filter(
        Q(receiver=request.user) | Q(sender=request.user)
    ).values('sender', 'receiver').annotate(last_message=Max('created_at'))

    for conv in conversations:
        other_id = conv['sender'] if conv['receiver'] == request.user.id else conv['receiver']
        other_user = User.objects.get(id=other_id)
        shop_name = ''
        if other_user.is_superuser:
            shop_name = 'Admin'
        else:
            try:
                po = other_user.user_profile
                if po.assigned_shop:
                    shop_name = po.assigned_shop.name
            except UserProfile.DoesNotExist:
                pass
        display_name = shop_name or other_user.get_full_name() or other_user.username

        last = Message.objects.filter(
            (Q(sender=request.user, receiver=other_user) |
             Q(sender=other_user, receiver=request.user))
        ).order_by('-created_at').first()

        unread = Message.objects.filter(sender=other_user, receiver=request.user, is_read=False).count()

        body_preview = ''
        if last:
            body_preview = last.body[:80] + ('...' if len(last.body) > 80 else '')

        contacts[other_id] = {
            'id': other_id,
            'username': display_name,
            'last_message_at': conv['last_message'],
            'last_message': body_preview,
            'unread': unread,
            'is_online': other_id in online_ids,
        }

    all_users = User.objects.exclude(id=request.user.id).select_related('user_profile__assigned_shop')
    for u in all_users:
        uid = u.id
        if uid not in contacts:
            shop_name = ''
            if u.is_superuser:
                shop_name = 'Admin'
            else:
                try:
                    pu = u.user_profile
                    if pu.assigned_shop:
                        shop_name = pu.assigned_shop.name
                except UserProfile.DoesNotExist:
                    pass
            contacts[uid] = {
                'id': uid,
                'username': shop_name or u.get_full_name() or u.username,
                'last_message_at': None,
                'last_message': '',
                'unread': 0,
                'is_online': uid in online_ids,
            }

    aware_min = timezone.make_aware(timezone.datetime.min, dt_timezone.utc)
    sorted_contacts = sorted(contacts.values(), key=lambda x: x['last_message_at'] or aware_min, reverse=True)

    selected_user_id = request.GET.get('user', '')
    messages = []
    selected_user = None
    selected_contact = None
    if selected_user_id:
        selected_user = get_object_or_404(User, id=selected_user_id)
        messages = Message.objects.filter(
            (Q(sender=request.user) & Q(receiver=selected_user)) |
            (Q(sender=selected_user) & Q(receiver=request.user))
        ).order_by('created_at')
        Message.objects.filter(sender=selected_user, receiver=request.user, is_read=False).update(is_read=True)
        selected_contact = contacts.get(int(selected_user_id))

    unread_count = Message.objects.filter(receiver=request.user, is_read=False).count()

    return render(request, 'stock_manager/chat.html', {
        'contacts': sorted_contacts,
        'selected_user': selected_user,
        'selected_contact': selected_contact,
        'messages': messages,
        'unread_count': unread_count,
        'page_title': 'Chat',
    })


@login_required
def send_message(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        receiver_id = data.get('receiver_id')
        body = data.get('body', '').strip()

        if receiver_id and body:
            receiver = get_object_or_404(User, id=receiver_id)
            msg = Message.objects.create(sender=request.user, receiver=receiver, body=body)

            channel_layer = get_channel_layer()
            sender_name = _get_display_name(request.user)
            async_to_sync(channel_layer.group_send)(
                f'user_{receiver_id}',
                {
                    'type': 'new_message',
                    'id': msg.id,
                    'sender': request.user.id,
                    'sender_name': sender_name,
                    'body': body,
                    'created_at': msg.created_at.isoformat(),
                    'audio_url': None,
                }
            )

            return JsonResponse({'status': 'sent', 'id': msg.id})

    return JsonResponse({'status': 'error'}, status=400)


@login_required
def send_bulk_message(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        body = data.get('body', '').strip()

        if body:
            users = User.objects.exclude(id=request.user.id)
            messages = [Message(sender=request.user, receiver=u, body=body) for u in users]
            Message.objects.bulk_create(messages)

            channel_layer = get_channel_layer()
            sender_name = _get_display_name(request.user)
            for u in users:
                async_to_sync(channel_layer.group_send)(
                    f'user_{u.id}',
                    {
                        'type': 'new_message',
                        'id': 0,
                        'sender': request.user.id,
                        'sender_name': sender_name,
                        'body': body,
                        'created_at': timezone.now().isoformat(),
                        'audio_url': None,
                    }
                )

            return JsonResponse({'status': 'sent', 'count': len(messages)})

    return JsonResponse({'status': 'error'}, status=400)


@login_required
def send_voice_note(request):
    if request.method == 'POST':
        receiver_id = request.POST.get('receiver_id')
        duration = request.POST.get('duration')
        audio = request.FILES.get('audio')

        if not receiver_id or not audio:
            return JsonResponse({'status': 'error', 'error': 'Missing receiver or audio'}, status=400)

        receiver = get_object_or_404(User, id=receiver_id)
        msg = Message.objects.create(
            sender=request.user,
            receiver=receiver,
            body='',
            audio_file=audio,
            duration=float(duration) if duration else None,
        )

        channel_layer = get_channel_layer()
        sender_name = _get_display_name(request.user)
        async_to_sync(channel_layer.group_send)(
            f'user_{receiver_id}',
            {
                'type': 'new_message',
                'id': msg.id,
                'sender': request.user.id,
                'sender_name': sender_name,
                'body': '',
                'created_at': msg.created_at.isoformat(),
                'audio_url': msg.audio_file.url,
            }
        )

        return JsonResponse({
            'status': 'sent',
            'id': msg.id,
            'audio_url': msg.audio_file.url,
            'duration': msg.duration,
        })

    return JsonResponse({'status': 'error'}, status=400)


@login_required
def get_messages(request, user_id):
    other_user = get_object_or_404(User, id=user_id)
    messages = Message.objects.filter(
        (Q(sender=request.user) & Q(receiver=other_user)) |
        (Q(sender=other_user) & Q(receiver=request.user))
    ).order_by('created_at')

    updated = Message.objects.filter(sender=other_user, receiver=request.user, is_read=False).update(is_read=True)
    if updated:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'user_{other_user.id}',
            {
                'type': 'messages_read',
                'user_id': request.user.id,
            }
        )

    msg_list = []
    for msg in messages:
        item = {
            'id': msg.id,
            'sender': msg.sender.id,
            'sender_name': _get_display_name(msg.sender),
            'body': msg.body,
            'created_at': msg.created_at.isoformat(),
            'is_read': msg.is_read,
        }
        if msg.audio_file:
            item['audio_url'] = msg.audio_file.url
            item['duration'] = msg.duration
        msg_list.append(item)

    typing = UserPresence.objects.filter(
        user=other_user, typing_to=request.user, is_online=True
    ).exists()

    return JsonResponse({'messages': msg_list, 'typing': typing})


@login_required
def send_typing(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        receiver_id = data.get('receiver_id')

        presence, _ = UserPresence.objects.get_or_create(user=request.user)
        presence.typing_to = get_object_or_404(User, id=receiver_id) if receiver_id else None
        presence.is_online = True
        presence.save()

        return JsonResponse({'status': 'ok'})

    return JsonResponse({'status': 'error'}, status=400)


@login_required
def mark_read(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        user_id = data.get('user_id')
        if user_id:
            Message.objects.filter(sender_id=user_id, receiver=request.user, is_read=False).update(is_read=True)

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'user_{user_id}',
                {
                    'type': 'messages_read',
                    'user_id': request.user.id,
                }
            )

            return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'error'}, status=400)


def _get_display_name(user):
    if user.is_superuser:
        return 'Admin'
    try:
        p = user.user_profile
        if p.assigned_shop:
            return p.assigned_shop.name
    except Exception:
        pass
    return user.get_full_name().strip() or user.username


@login_required
def get_unread_count(request):
    count = Message.objects.filter(receiver=request.user, is_read=False).count()
    return JsonResponse({'unread': count})


@login_required
def calls_view(request):
    calls = Call.objects.filter(
        Q(caller=request.user) | Q(callee=request.user)
    ).select_related('caller', 'callee').order_by('-started_at')[:50]

    incoming_ringing = Call.objects.filter(callee=request.user, status='ringing')

    return render(request, 'stock_manager/calls.html', {
        'calls': calls,
        'incoming_ringing': incoming_ringing,
        'page_title': 'Calls',
    })


@login_required
def initiate_call(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        callee_username = data.get('callee_username', '').strip()
        call_type = data.get('call_type', 'voice')

        if callee_username:
            try:
                callee = User.objects.get(username=callee_username)
            except User.DoesNotExist:
                return JsonResponse({'status': 'error', 'error': 'User not found'}, status=404)

            call = Call.objects.create(
                caller=request.user,
                callee=callee,
                call_type=call_type,
                status='ringing',
            )

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'user_{callee.id}',
                {
                    'type': 'incoming_call',
                    'call_id': call.id,
                    'caller': request.user.get_full_name() or request.user.username,
                    'caller_id': request.user.id,
                    'call_type': call_type,
                }
            )

            return JsonResponse({
                'status': 'initiated',
                'call_id': call.id,
            })

    return JsonResponse({'status': 'error', 'error': 'Invalid request'}, status=400)


@login_required
def call_room(request, call_id):
    call = get_object_or_404(Call, id=call_id)

    if call.caller != request.user and call.callee != request.user:
        return redirect('calls')

    if call.status == 'ringing' and call.callee == request.user:
        call.status = 'accepted'
        call.save()

    return render(request, 'stock_manager/call_room.html', {
        'call': call,
        'other_user': call.callee if call.caller == request.user else call.caller,
        'page_title': 'Call Room',
    })


@login_required
def end_call(request, call_id):
    call = get_object_or_404(Call, id=call_id)
    if call.caller == request.user or call.callee == request.user:
        call.status = 'ended'
        call.ended_at = timezone.now()
        call.save()

        other_id = call.callee.id if call.caller == request.user else call.caller.id
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
        except Exception:
            pass

    return JsonResponse({'status': 'ended'})


@login_required
def call_signal(request, call_id):
    call = get_object_or_404(Call, id=call_id)

    if call.caller != request.user and call.callee != request.user:
        return JsonResponse({'error': 'Not a participant'}, status=403)

    if request.method == 'POST':
        data = json.loads(request.body)
        call.refresh_from_db()
        signal_data = call.signaling_data
        signal_data[data.get('type', 'unknown')] = data.get('data', {})
        call.signaling_data = signal_data
        call.save()
        return JsonResponse({'status': 'ok'})

    return JsonResponse({'signaling': call.signaling_data, 'call_status': call.status})


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


@login_required
def check_incoming_call(request):
    ringing = Call.objects.filter(callee=request.user, status='ringing').select_related('caller').first()
    if ringing:
        return JsonResponse({
            'ringing': True,
            'call_id': ringing.id,
            'caller': ringing.caller.get_full_name() or ringing.caller.username,
            'call_type': ringing.call_type,
        })
    return JsonResponse({'ringing': False})
