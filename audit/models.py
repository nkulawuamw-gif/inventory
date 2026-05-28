from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class AuditLog(models.Model):
    """
    Centralized audit trail for the entire system.
    Records every critical action performed by users.
    """

    ACTION_CHOICES = [
        ("CREATE", "Create"),
        ("UPDATE", "Update"),
        ("DELETE", "Delete"),
        ("LOGIN", "Login"),
        ("LOGOUT", "Logout"),
        ("SALE", "Sale"),
        ("REFUND", "Refund"),
        ("VOID", "Void Transaction"),
        ("PAYMENT", "Payment"),
        ("STOCK_IN", "Stock In"),
        ("STOCK_OUT", "Stock Out"),
        ("ADJUSTMENT", "Inventory Adjustment"),
        ("PRICE_CHANGE", "Price Change"),
        ("TRANSFER", "Stock Transfer"),
        ("APPROVAL", "Approval"),
        ("OTHER", "Other"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs"
    )

    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES
    )

    module = models.CharField(
        max_length=100,
        help_text="Module where action occurred e.g Sales, Inventory, Finance"
    )

    object_type = models.CharField(
        max_length=100,
        blank=True
    )

    object_id = models.CharField(
        max_length=100,
        blank=True
    )

    description = models.TextField()

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True
    )

    old_value = models.JSONField(
        null=True,
        blank=True
    )

    new_value = models.JSONField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        db_index=True
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["module"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.action} - {self.module} - {self.created_at}"


class LoginSession(models.Model):
    """
    Tracks user login/logout activity.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    login_time = models.DateTimeField(
        default=timezone.now
    )

    logout_time = models.DateTimeField(
        null=True,
        blank=True
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True
    )

    user_agent = models.TextField(
        blank=True
    )

    class Meta:
        ordering = ["-login_time"]

    def __str__(self):
        return f"{self.user} - {self.login_time}"


class DailyAuditSummary(models.Model):
    """
    Stores daily audit statistics for dashboards.
    """

    date = models.DateField(unique=True)

    total_sales = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0
    )

    sales_count = models.PositiveIntegerField(
        default=0
    )

    refunds_count = models.PositiveIntegerField(
        default=0
    )

    void_transactions = models.PositiveIntegerField(
        default=0
    )

    stock_adjustments = models.PositiveIntegerField(
        default=0
    )

    price_changes = models.PositiveIntegerField(
        default=0
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return str(self.date)
