from django.db import models


class CrawlResult(models.Model):
    company_name = models.CharField(max_length=100)       # 사용자 입력 회사명
    stock_name = models.CharField(max_length=100)         # 실제 종목명
    raw_comments = models.JSONField()                     # 원본 댓글 목록
    clean_comments = models.JSONField()                   # 정제 댓글 목록
    augmented_comments = models.JSONField()               # 증강 댓글 목록
    iqr_info = models.JSONField(default=dict)             # IQR 임계값 정보
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.stock_name} ({self.created_at:%Y-%m-%d %H:%M})"