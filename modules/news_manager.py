import feedparser
from modules.llm_manager import LLMManager
from modules.metrics_manager import DataUsageTracker
import requests
from bs4 import BeautifulSoup
import mysql.connector
import json
import logging
from datetime import datetime, timedelta, timezone
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NewsDatabase:
    """
    뉴스 항목 및 요약 캐싱을 위한 데이터베이스 상호 작용을 관리합니다.
    
    속성:
        config (dict): 로드된 구성.
        db_config (dict): 데이터베이스 연결 세부 정보.
    """
    def __init__(self, config_file='config.json'):
        self.config = self._load_config(config_file)
        self.db_config = self.config.get('news_db')
        self.ensure_table_exists()

    def _load_config(self, config_file):
        """JSON 파일에서 구성을 로드합니다."""
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return json.load(f)
        return {}

    def ensure_table_exists(self):
        """
        필요한 데이터베이스 테이블(tb_news, tb_summary_cache)이 존재하는지 확인합니다.
        누락된 경우 생성합니다.
        """
        try:
            # 먼저 특정 DB에 연결 시도
            conn = self.get_connection()
            if not conn:
                # 연결 실패 시 DB가 없을 수 있음. 서버 루트에 연결 시도.
                if not self._create_database():
                    return
                conn = self.get_connection()
            
            if conn:
                cursor = conn.cursor()
                # 메인 뉴스 테이블
                create_table_query = """
                CREATE TABLE IF NOT EXISTS tb_news (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    link VARCHAR(500) NOT NULL,
                    published_date VARCHAR(100),
                    summary TEXT,
                    content TEXT,
                    source VARCHAR(50),
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_link (link)
                )
                """
                cursor.execute(create_table_query)
                
                # 마이그레이션: comment 컬럼이 없으면 추가
                try:
                    cursor.execute("SELECT comment FROM tb_news LIMIT 1")
                    cursor.fetchall()
                except:
                    logger.info("Adding comment column...")
                    cursor.execute("ALTER TABLE tb_news ADD COLUMN comment TEXT")
                    
                conn.commit()
                cursor.close()

                # 캐시 테이블
                cursor = conn.cursor()
                create_cache_table_query = """
                CREATE TABLE IF NOT EXISTS tb_summary_cache (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    link_hash VARCHAR(255) NOT NULL, 
                    link TEXT NOT NULL,
                    summary TEXT,
                    model VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_link_hash (link_hash)
                )
                """
                cursor.execute(create_cache_table_query)
                conn.commit()
                cursor.close()
                
                conn.close()
                logger.info("Tables checked/created.")
        except Exception as e:
            logger.error(f"Table setup error: {e}")

    def _create_database(self):
        """데이터베이스가 존재하지 않으면 생성합니다."""
        try:
            # 데이터베이스 없이 연결
            conn = mysql.connector.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password']
            )
            cursor = conn.cursor()
            db_name = self.db_config['database']
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Create DB error: {e}")
            return False

    def get_summary_from_cache(self, link):
        """
        주어진 링크에 대한 캐시된 요약을 검색합니다.
        
        Args:
            link (str): 뉴스 기사의 URL.
            
        Returns:
            str: 캐시된 요약 또는 찾을 수 없는 경우 None.
        """
        import hashlib
        link_hash = hashlib.md5(link.encode('utf-8')).hexdigest()
        
        conn = self.get_connection()
        if not conn: return None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT summary, model, created_at FROM tb_summary_cache WHERE link_hash = %s", (link_hash,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            if result:
                return {
                    'summary': result['summary'],
                    'model': result.get('model', 'unknown'),
                    'created_at': result['created_at']
                }
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None

    def save_summary_to_cache(self, link, summary, model="unknown"):
        """
        요약을 캐시 테이블에 저장합니다.
        
        Args:
            link (str): 기사의 URL.
            summary (str): 생성된 요약 텍스트.
            model (str): 생성에 사용된 모델.
        """
        import hashlib
        link_hash = hashlib.md5(link.encode('utf-8')).hexdigest()
        
        conn = self.get_connection()
        if not conn: return False
        
        try:
            cursor = conn.cursor()
            # Upsert
            query = """
            INSERT INTO tb_summary_cache (link_hash, link, summary, model)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE summary=%s, model=%s, created_at=NOW()
            """
            cursor.execute(query, (link_hash, link, summary, model, summary, model))
            conn.commit()
            
            # 간단한 정리: 최근 100개 항목만 유지
            cursor.execute("SELECT count(*) FROM tb_summary_cache")
            count = cursor.fetchone()[0]
            if count > 100:
                # 가장 오래된 항목 삭제
                delete_query = """
                DELETE FROM tb_summary_cache 
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM tb_summary_cache ORDER BY created_at DESC LIMIT 100
                    ) foo
                )
                """
                cursor.execute(delete_query)
                conn.commit()

            cursor.close()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Save error: {e}")
            return False

    def get_connection(self):
        """데이터베이스 연결을 설정하고 반환합니다."""
        try:
            conn = mysql.connector.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database']
            )
            return conn
        except mysql.connector.Error as err:
            logger.error(f"DB Connection Error: {err}")
            return None

    def save_article(self, article):
        """
        뉴스 기사를 메인 뉴스 테이블(tb_news)에 저장합니다.
        
        Args:
            article (dict): 기사 세부 정보(제목, 링크, 게시일, 요약, 내용 등)를 포함하는 딕셔너리.
        """
        conn = self.get_connection()
        if not conn:
            return False
        
        try:
            cursor = conn.cursor()
            query = """
            INSERT INTO tb_news (title, link, published_date, summary, content, source, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                title=%s, published_date=%s, summary=%s, content=%s, source=%s, comment=%s, created_at=NOW()
            """
            values = (
                article.get('title'),
                article.get('link'),
                article.get('published'),
                article.get('summary', ''),
                article.get('content', ''),
                article.get('source', ''),
                article.get('comment', ''),
                
                article.get('title'),
                article.get('published'),
                article.get('summary', ''),
                article.get('content', ''),
                article.get('source', ''),
                article.get('comment', '')
            )
            cursor.execute(query, values)
            conn.commit()
            return True
        except mysql.connector.Error as err:
            logger.error(f"Save Error: {err}")
            return False
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_saved_articles(self):
        """tb_news에서 저장된 모든 기사를 최신순으로 검색합니다."""
        conn = self.get_connection()
        if not conn:
            return []
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM tb_news ORDER BY created_at DESC")
            return cursor.fetchall()
        finally:
            if conn:
                conn.close()

class NewsFetcher:
    """
    뉴스 피드 가져오기 및 기사 내용 추출을 처리합니다.
    
    속성:
        sources (dict): 뉴스 소스 및 해당 RSS URL의 딕셔너리.
        llm_manager (LLMManager): AI 작업을 위한 인스턴스.
    """
    def __init__(self, config_file='config.json'):
        self.config = self._load_config(config_file)
        self.sources = {
            "매일경제": "https://www.mk.co.kr/rss/30000001/",
            "한겨레": "https://www.hani.co.kr/rss",
            "GeekNews": "https://news.hada.io/rss/news",
        }
        self.llm_manager = LLMManager()
        self.feed_headers = {} # 소스별 ETag/Last-Modified 저장

    def _load_config(self, config_file):
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return json.load(f)
        return {}

    def fetch_feeds(self, source_name):
        """
        Conditional GET을 사용하여 주어진 소스 이름에 대한 RSS 피드를 가져옵니다.

        Args:
            source_name (str): self.sources 중 하나와 일치하는 키.

        Returns:
            list: 상위 10개 뉴스 항목(dict)의 목록, 또는 변경 사항이 없는 경우 None.
        """
        url = self.sources.get(source_name)
        if not url:
            return []
        
        # 차단을 피하기 위해 헤더와 함께 requests 사용 (특히 YTN)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        # Conditional GET 헤더 추가
        if source_name in self.feed_headers:
            cached_headers = self.feed_headers[source_name]
            if 'ETag' in cached_headers:
                headers['If-None-Match'] = cached_headers['ETag']
            if 'Last-Modified' in cached_headers:
                headers['If-Modified-Since'] = cached_headers['Last-Modified']

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            
            # 304 Not Modified 확인
            if resp.status_code == 304:
                logger.info(f"Feed {source_name} not modified (304).")
                return None # 변경 없음 신호

            resp.raise_for_status()
            
            # 헤더 업데이트
            new_headers = {}
            if 'ETag' in resp.headers:
                new_headers['ETag'] = resp.headers['ETag']
            if 'Last-Modified' in resp.headers:
                new_headers['Last-Modified'] = resp.headers['Last-Modified']
            
            if new_headers:
                self.feed_headers[source_name] = new_headers
            
            # 사용량 추적 (200 OK인 경우에만)
            tracker = DataUsageTracker()
            tracker.add_rx(len(resp.content))
            
            feed = feedparser.parse(resp.content)
        except Exception as e:
            logger.error(f"Error fetching feed for {source_name}: {e}")
            return []

        entries = []
        for entry in feed.entries[:5]: # 5개로 제한
            published = entry.get('published', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            
            # KST 변환 로직 추가
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try:
                    # feedparser는 published_parsed를 UTC struct_time으로 반환함
                    dt_utc = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    
                    # KST (UTC+9)로 변환
                    kst_tz = timezone(timedelta(hours=9))
                    dt_kst = dt_utc.astimezone(kst_tz)
                    
                    published = dt_kst.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    # 변환 실패 시 원본 문자열 유지
                    pass

            entries.append({
                'title': entry.title,
                'link': entry.link,
                'published': published,
                'source': source_name
            })
        return entries

    def get_full_text(self, url):
        """
        뉴스 기사 URL에서 전체 텍스트 콘텐츠를 추출합니다.
        
        Google 뉴스 리디렉션 및 다양한 HTML 구조를 처리합니다.

        Args:
            url (str): 기사 URL.

        Returns:
            str: 추출된 텍스트 콘텐츠 또는 오류 메시지.
        """
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            response = requests.get(url, headers=headers, timeout=10)
            
            # Google 뉴스 리디렉션 처리 (JS 리디렉션)
            if "news.google.com" in response.url or "news.google.com" in url:
                # 응답 콘텐츠에서 실제 URL 찾기 시도
                import re
                # 자주 사용되는 패턴: window.location.replace("..."); 또는 <a href="...">
                # 메인 리디렉션 링크를 찾기 위한 간단한 시도
                match = re.search(r'window\.location\.replace\("(.+?)"\)', response.text)
                if match:
                    real_url = match.group(1).replace('\\u003d', '=').replace('\\x3d', '=')
                    logger.info(f"Redirecting Google URL to: {real_url}")
                    response = requests.get(real_url, headers=headers, timeout=10)
                else:
                    # 폴백: 위 방법이 실패하면 일반 href 찾기
                    soup_redirect = BeautifulSoup(response.content, 'html.parser')
                    # 위험하지만 noscript 블록에 대해 가끔 작동함
                    links = soup_redirect.find_all('a')
                    if links and len(links) < 5: # 페이지가 거의 비어 있는 경우
                        real_url = links[0].get('href')
                        if real_url:
                             response = requests.get(real_url, headers=headers, timeout=10)
                             DataUsageTracker().add_rx(len(response.content))
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # 스크립트 및 스타일 제거
            for script in soup(["script", "style", "nav", "header", "footer"]):
                script.decompose()

            # 개선된 추출 로직
            # 1. itemprop="articleBody" 찾기 (MK와 같은 현대적인 뉴스 사이트에서 일반적)
            article_body = soup.find(attrs={"itemprop": "articleBody"})
            
            # 2. 'article' 태그 찾기
            article_tag = soup.find('article')
            
            # 3. 특정 클래스 찾기 (MK 등)
            class_candidates = soup.find_all('div', class_=lambda x: x and x in ['art_txt', 'view_txt', 'news_view'])

            target_element = None
            if article_body:
                target_element = article_body
            elif article_tag:
                target_element = article_tag
            elif class_candidates:
                # 여러 후보를 필요한 경우 더미 태그로 감싸거나, 첫 번째/가장 큰 것을 선택
                # 간단하게 후보를 찾으면 첫 번째를 메인으로 처리하거나 리스트로 처리
                # 하지만 그들을 위한 새로운 soup 객체를 만드는 것이 더 깔끔함
                target_element = soup.new_tag('div')
                for c in class_candidates:
                    target_element.append(c)

            text_content = []
            
            if target_element:
                # 전처리 단계: HTML 구조를 Markdown 스타일 텍스트로 변환
                
                # 코드 블록 처리 (<pre>)
                for pre in target_element.find_all('pre'):
                    code_text = pre.get_text()
                    # 콘텐츠를 펜스 코드 블록으로 교체
                    pre.string = f"\n```\n{code_text}\n```\n"
                
                # 리스트 처리 (<ul>, <ol>) - 간단한 근사치
                for ul in target_element.find_all('ul'):
                    for li in ul.find_all('li'):
                        li.string = f"- {li.get_text()}"
                
                # 제목 처리 (h1-h3) - 선택 사항이지만 좋음
                for i in range(1, 4):
                    for h in target_element.find_all(f'h{i}'):
                        h.string = f"\n{'#' * i} {h.get_text()}\n"
                
                text = target_element.get_text(separator='\n\n')
                
                # 과도한 개행 정리
                import re
                text = re.sub(r'\n{3,}', '\n\n', text)
                text_content.append(text.strip())

            else:
                # 모든 p 태그로 폴백
                paragraphs = soup.find_all('p')
                for p in paragraphs:
                    txt = p.get_text().strip()
                    if len(txt) > 40:
                        text_content.append(txt)
            
            text = '\n\n'.join(text_content)
            
            # 캡처된 경우 MK의 내부 AI 요약 제거 (종종 "뉴스 요약쏙"으로 시작)
            if "뉴스 요약쏙" in text:
                # 상단에 나타나는 경우 헤더 쓰레기를 제거하기 위한 간단한 분할
                parts = text.split("뉴스 요약쏙")
                if len(parts) > 1:
                    # 보통 요약은 상단에 있고, 실제 콘텐츠는 그 뒤에?
                    # 사실 MK는 요약을 대부분 별도 div에 넣음.
                    # 하지만 'articleBody'를 잡았다면 깨끗할 것임.
                    pass
            
            if not text and "news.google.com" in url:
                return "⚠️ Content extraction failed. Google News often blocks full-text extraction tools. Please use the 'Link' button to read the original article."

            return text if text else "Could not extract text content. Site structure might be complex."
        except Exception as e:
            logger.error(f"Error fetching text: {e}")
            return f"Error fetching content: {e}"

    def generate_summary(self, text, model, link=None, force_refresh=False):
        """
        LLM을 사용하여 3개의 글머리 기호 요약을 생성합니다.
        Returns:
            dict: { 'text': str, 'meta': dict }
        """
        import time
        
        # 1. 링크가 제공되고 강제 새로고침이 아닌 경우 캐시 확인
        if link and not force_refresh:
            db = NewsDatabase()
            cached_data = db.get_summary_from_cache(link)
            if cached_data:
                # cached_data는 { 'summary', 'model', 'created_at' }임
                return {
                    'text': cached_data['summary'],
                    'meta': {
                        'source': 'Cache',
                        'model': cached_data['model'],
                        'time': 'N/A',
                        'host': 'DB'
                    }
                }
        
        if not text or len(text) < 100:
            return {'text': "Text too short to summarize.", 'meta': {}}
        
        # 작은 모델(0.5b)을 위한 더 강력한 프롬프트
        prompt = f"""### System:
You are a summary assistant. Output ONLY the summary in Korean. Do not say anything else.

### Instruction:
Summarize the content below into 3 bullet points using KOREAN ONLY.
- NO Chinese. NO English.
- NO introduction (e.g. "Here is the summary").
- NO conclusion.
- Format: "- Summary point..."

### Content:
{text[:3000]}

### Response:
"""
        start_time = time.time()
        summary = self.llm_manager.generate_response(prompt, model)
        end_time = time.time()
        elapsed = round(end_time - start_time, 2)
        
        current_host = self.llm_manager.current_host
        
        # 지속성을 위해 요약에 메타데이터 바닥글 추가
        # "작은" 느낌을 위해 마크다운 기울임꼴 사용
        footer = f"\n\n*(⏱ {elapsed}s | {model} | {current_host})*"
        full_summary = summary + footer
        
        # 2. 링크가 제공된 경우 캐시 저장 (존재하면 업데이트)
        if link and full_summary:
            db = NewsDatabase()
            db.save_summary_to_cache(link, full_summary, model)
            
        return {
            'text': full_summary,
            'meta': {
                'source': 'Live',
                'model': model,
                'time': f"{elapsed}s",
                'host': current_host
            }
        }


