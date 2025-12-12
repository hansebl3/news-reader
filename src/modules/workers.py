import time
from modules.news_manager import NewsDatabase

def auto_sum_worker(news_items, model, result_queue, stop_event, fetcher_instance):
    """뉴스 텍스트를 가져오고 요약하는 백그라운드 스레드"""
    

    for item in news_items:
        if stop_event.is_set():
            break
            
        link = item['link']
        
        try:
            # 1. DB 캐시 먼저 확인
            db_local = NewsDatabase()
            cached_data = db_local.get_summary_from_cache(link)
            if cached_data:
                # generate_summary 반환 형식에 맞게 래핑
                formatted_result = {
                    'text': cached_data['summary'],
                    'meta': {
                        'source': 'Cache',
                        'time': 'N/A',
                        'model': cached_data.get('model', 'Unknown'),
                        'host': 'DB'
                    },
                    'full_text': None
                }
                result_queue.put((link, formatted_result))
                continue
            
            # 2. 텍스트 가져오기 (백그라운드)
            text = fetcher_instance.get_full_text(link)
            
            # 3. {text, meta} 생성
            summary_data = fetcher_instance.generate_summary(text, model, link=link)
            
            # 메인 스레드가 세션 상태에 캐시할 수 있도록 전체 텍스트를 결과에 추가
            if summary_data:
                summary_data['full_text'] = text
                result_queue.put((link, summary_data))
            
            time.sleep(1) # 양보 (Yield)
        except Exception as e:
            print(f"Auto sum error: {e}")
