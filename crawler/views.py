from django.shortcuts import render
from .services import (
    fetch_visible_comments,
    filter_inappropriate,
    clean_with_pandas,
    augment_comments,
    summarize_comments,
)
from .models import CrawlResult


def index(request):
    return render(request, 'crawler/index.html')


def crawl(request):
    if request.method != 'POST':
        return render(request, 'crawler/index.html')

    company = request.POST.get('company', '').strip()

    # F101: 입력값 검증
    if not company:
        return render(request, 'crawler/index.html', {'error': '회사명을 입력해주세요.'})

    try:
        # F103, F104: 크롤링
        stock_name, raw_comments = fetch_visible_comments(company, limit=20)

        # F111: 검색 결과 없음 처리
        if not raw_comments:
            return render(request, 'crawler/index.html', {
                'error': f'"{company}"에 대한 댓글 데이터를 찾을 수 없습니다.'
            })

        # F105: 전처리 (부적절 필터 → pandas 정제)
        filtered = filter_inappropriate(list(raw_comments))
        clean_comments, iqr_info = clean_with_pandas(filtered)

        # F106: 데이터 증강
        augmented_comments = augment_comments(clean_comments)

        # F110: 댓글 요약
        summary = summarize_comments(raw_comments)

        # F102: DB 저장
        CrawlResult.objects.create(
            company_name=company,
            stock_name=stock_name,
            raw_comments=raw_comments,
            clean_comments=clean_comments,
            augmented_comments=augmented_comments,
            iqr_info=iqr_info,
        )

        # F107: 통합 결과 출력
        context = {
            'company': company,
            'stock_name': stock_name,
            'raw': raw_comments,
            'cleaned': clean_comments,
            'augmented': augmented_comments,
            'summary': summary,
            'iqr_info': iqr_info,
        }
        return render(request, 'crawler/result.html', context)

    except Exception as e:
        # F111: 예외 처리
        return render(request, 'crawler/index.html', {
            'error': f'오류가 발생했습니다: {str(e)}'
        })