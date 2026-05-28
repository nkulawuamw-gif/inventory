from .models import AuditLog


def log_activity(
    user,
    action,
    module,
    description,
    object_type="",
    object_id="",
    old_value=None,
    new_value=None,
    ip_address=None,
):
    AuditLog.objects.create(
        user=user,
        action=action,
        module=module,
        description=description,
        object_type=object_type,
        object_id=object_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
    )
