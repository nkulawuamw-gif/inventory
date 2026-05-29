import os
import json
from datetime import datetime

from django.db import models
from django.conf import settings
from django.core.files.base import ContentFile


class SystemVersion(models.Model):
    version = models.CharField(max_length=20, unique=True)
    release_date = models.DateField(null=True, blank=True)
    changelog = models.TextField(blank=True, default='')
    is_current = models.BooleanField(default=False)
    is_available = models.BooleanField(default=False)
    download_url = models.URLField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'System Version'
        verbose_name_plural = 'System Versions'

    def __str__(self):
        return f'v{self.version}'


class UpdateRecord(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('rolled_back', 'Rolled Back'),
    ]

    version_from = models.CharField(max_length=20)
    version_to = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    changelog = models.TextField(blank=True, default='')
    backup_file = models.FileField(upload_to='backups/', null=True, blank=True)
    backup_size = models.PositiveIntegerField(default=0)
    error_log = models.TextField(blank=True, default='')
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.version_from} → {self.version_to} ({self.status})'
