from django.db import models


class AIReport(models.Model):
    """AI Identifier prediction report, owned by an NHIT RAMS username.

    Username is always taken from the JWT at write time — never from the
    request body. There is no ai_users table; username is a plain string
    matching the legacy PHP application's username.
    """

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, db_index=True)
    prediction = models.CharField(max_length=255)
    confidence = models.FloatField()
    image_path = models.CharField(max_length=512, blank=True, default='')
    report_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ai_reports'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['username', '-created_at'], name='ai_reports_user_created_idx'),
        ]

    def __str__(self):
        return f'AIReport {self.id} ({self.username}) — {self.prediction}'
