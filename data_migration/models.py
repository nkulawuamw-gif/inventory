from django.db import models
from django.conf import settings


class ExportArchive(models.Model):
    file = models.FileField(upload_to='exports/')
    filesize = models.PositiveIntegerField(default=0)
    include_products = models.BooleanField(default=True)
    include_categories = models.BooleanField(default=True)
    include_users = models.BooleanField(default=True)
    include_roles = models.BooleanField(default=True)
    include_sales = models.BooleanField(default=True)
    include_transactions = models.BooleanField(default=True)
    include_chat = models.BooleanField(default=True)
    include_notifications = models.BooleanField(default=True)
    include_settings = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def filename(self):
        if self.file:
            import os
            return os.path.basename(self.file.name)
        return ''


class ImportLog(models.Model):
    MODE_CHOICES = [
        ('add', 'Add New Records Only'),
        ('merge', 'Merge Existing Records'),
        ('replace', 'Full Replace'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('preview', 'Preview Ready'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('rolled_back', 'Rolled Back'),
    ]

    archive = models.ForeignKey(ExportArchive, on_delete=models.SET_NULL, null=True, blank=True)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    summary = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_log = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
