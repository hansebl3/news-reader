import feedparser
import subprocess
from modules.llm_manager import LLMManager
from modules.metrics_manager import DataUsageTracker
import requests
from bs4 import BeautifulSoup
import mysql.connector
import json
import logging
from datetime import datetime
import openai
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NewsDatabase:
    """
    Manages database interactions for news items and summary caching.
    
    Attributes:
        config (dict): Loaded configuration.
        db_config (dict): Database connection details.
    """
    def __init__(self, config_file='config.json'):
        self.config = self._load_config(config_file)
        self.db_config = self.config.get('news_db')
        self.ensure_table_exists()

    def _load_config(self, config_file):
        """Loads configuration from a JSON file."""
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return json.load(f)
        return {}

    def ensure_table_exists(self):
        """
        Ensures that the necessary database tables (tb_news, tb_summary_cache) exist.
        Creates them if they are missing.
        """
        try:
            # First try connecting to the specific DB
            conn = self.get_connection()
            if not conn:
                # If connection fails, maybe DB doesn't exist. Try connecting to server root.
                if not self._create_database():
                    return
                conn = self.get_connection()
            
            if conn:
                cursor = conn.cursor()
                # Main News Table
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
                
                # Migration: Add comment column if not exists
                try:
                    cursor.execute("SELECT comment FROM tb_news LIMIT 1")
                    cursor.fetchall()
                except:
                    logger.info("Adding comment column...")
                    cursor.execute("ALTER TABLE tb_news ADD COLUMN comment TEXT")
                    
                conn.commit()
                cursor.close()

                # Cache Table
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
        """Creates the database if it does not exist."""
        try:
            # Connect without database
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
        Retrieves a cached summary for a given link.
        
        Args:
            link (str): The URL of the news article.
            
        Returns:
            str: The cached summary, or None if not found.
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
        Saves a summary to the cache table.
        
        Args:
            link (str): The URL of the article.
            summary (str): The generated summary text.
            model (str): The model used for generation.
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
            
            # Simple cleanup: keep only last 100 entries
            cursor.execute("SELECT count(*) FROM tb_summary_cache")
            count = cursor.fetchone()[0]
            if count > 100:
                # Delete oldest
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
        """Establishes and returns a database connection."""
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
        Saves a news article to the main news table (tb_news).
        
        Args:
            article (dict): Dictionary containing article details (title, link, published, summary, content, etc.)
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
        """Retrieves all saved articles from tb_news, ordered by recency."""
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
    Handles fetching news feeds and extracting article content.
    
    Attributes:
        sources (dict): Dictionary of news sources and their RSS URLs.
        llm_manager (LLMManager): Instance for AI operations.
    """
    def __init__(self, config_file='config.json'):
        self.config = self._load_config(config_file)
        self.sources = {
            "매일경제": "https://www.mk.co.kr/rss/30000001/",
            "한겨레": "https://www.hani.co.kr/rss",
            "GeekNews": "https://news.hada.io/rss/news",
        }
        self.llm_manager = LLMManager()
        self.feed_headers = {} # Store ETag/Last-Modified per source

    def _load_config(self, config_file):
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return json.load(f)
        return {}

    def fetch_feeds(self, source_name):
        """
        Fetches the RSS feed for a given source name using Conditional GET.

        Args:
            source_name (str): Key matching one of self.sources.

        Returns:
            list: A list of the top 10 news items (dicts), or None if unchanged.
        """
        url = self.sources.get(source_name)
        if not url:
            return []
        
        # Use requests to fetch with headers to avoid blocking (especially YTN)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        # Add Conditional GET headers
        if source_name in self.feed_headers:
            cached_headers = self.feed_headers[source_name]
            if 'ETag' in cached_headers:
                headers['If-None-Match'] = cached_headers['ETag']
            if 'Last-Modified' in cached_headers:
                headers['If-Modified-Since'] = cached_headers['Last-Modified']

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            
            # Check for 304 Not Modified
            if resp.status_code == 304:
                logger.info(f"Feed {source_name} not modified (304).")
                return None # Signal no change

            resp.raise_for_status()
            
            # Update Headers
            new_headers = {}
            if 'ETag' in resp.headers:
                new_headers['ETag'] = resp.headers['ETag']
            if 'Last-Modified' in resp.headers:
                new_headers['Last-Modified'] = resp.headers['Last-Modified']
            
            if new_headers:
                self.feed_headers[source_name] = new_headers
            
            # Track Usage (Only if 200 OK)
            tracker = DataUsageTracker()
            tracker.add_rx(len(resp.content))
            
            feed = feedparser.parse(resp.content)
        except Exception as e:
            logger.error(f"Error fetching feed for {source_name}: {e}")
            return []

        entries = []
        for entry in feed.entries[:10]: # Limit to 10
            entries.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.get('published', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                'source': source_name
            })
        return entries

    def get_full_text(self, url):
        """
        Extracts the full text content from a news article URL.
        
        Handles Google News redirects and various HTML structures.

        Args:
            url (str): The article URL.

        Returns:
            str: Extracted text content or error message.
        """
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            response = requests.get(url, headers=headers, timeout=10)
            
            # Handle Google News Redirects (JS Redirect)
            if "news.google.com" in response.url or "news.google.com" in url:
                # Try to find the real URL in the response content
                import re
                # Pattern often used: window.location.replace("..."); or <a href="...">
                # Simple attempt to find the main redirect link
                match = re.search(r'window\.location\.replace\("(.+?)"\)', response.text)
                if match:
                    real_url = match.group(1).replace('\\u003d', '=').replace('\\x3d', '=')
                    logger.info(f"Redirecting Google URL to: {real_url}")
                    response = requests.get(real_url, headers=headers, timeout=10)
                else:
                    # Fallback: look for generic hrefs if the above fails
                    soup_redirect = BeautifulSoup(response.content, 'html.parser')
                    # This is risky but sometimes works for noscript blocks
                    links = soup_redirect.find_all('a')
                    if links and len(links) < 5: # If page is bare bones
                        real_url = links[0].get('href')
                        if real_url:
                             response = requests.get(real_url, headers=headers, timeout=10)
                             DataUsageTracker().add_rx(len(response.content))
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove scripts and styles
            for script in soup(["script", "style", "nav", "header", "footer"]):
                script.decompose()

            # Improved Extraction Logic
            # Improved Extraction Logic
            # 1. Look for itemprop="articleBody" (Common in modern news sites like MK)
            article_body = soup.find(attrs={"itemprop": "articleBody"})
            
            # 2. Look for 'article' tag
            article_tag = soup.find('article')
            
            # 3. Look for specific classes (MK, etc.)
            class_candidates = soup.find_all('div', class_=lambda x: x and x in ['art_txt', 'view_txt', 'news_view'])

            target_element = None
            if article_body:
                target_element = article_body
            elif article_tag:
                target_element = article_tag
            elif class_candidates:
                # Wrap multiple candidates in a dummy tag if needed, or just pick the first/largest
                # For simplicity, if we found candidates, let's treat the first one as main or handle them list
                # But creating a new soup object for them is cleaner
                target_element = soup.new_tag('div')
                for c in class_candidates:
                    target_element.append(c)

            text_content = []
            
            if target_element:
                # Pre-process Step: Convert HTML structures to Markdown-ish text
                
                # Handle Code Blocks (<pre>)
                for pre in target_element.find_all('pre'):
                    code_text = pre.get_text()
                    # Replace content with fenced code block
                    pre.string = f"\n```\n{code_text}\n```\n"
                
                # Handle Lists (<ul>, <ol>) - Simple approximation
                for ul in target_element.find_all('ul'):
                    for li in ul.find_all('li'):
                        li.string = f"- {li.get_text()}"
                
                # Handle Headings (h1-h3) - Optional but nice
                for i in range(1, 4):
                    for h in target_element.find_all(f'h{i}'):
                        h.string = f"\n{'#' * i} {h.get_text()}\n"
                
                text = target_element.get_text(separator='\n\n')
                
                # Cleanup excessive newlines
                import re
                text = re.sub(r'\n{3,}', '\n\n', text)
                text_content.append(text.strip())

            else:
                # Fallback to all p tags
                paragraphs = soup.find_all('p')
                for p in paragraphs:
                    txt = p.get_text().strip()
                    if len(txt) > 40:
                        text_content.append(txt)
            
            text = '\n\n'.join(text_content)
            
            # Remove MK's internal AI summary if captured (often starts with "뉴스 요약쏙")
            if "뉴스 요약쏙" in text:
                # Simple split to remove header trash if it appears at top
                parts = text.split("뉴스 요약쏙")
                if len(parts) > 1:
                    # usually the summary is at top, real content after? 
                    # Actually MK puts summary in a separate div mostly. 
                    # But if we grabbed 'articleBody', it might be clean.
                    pass
            
            if not text and "news.google.com" in url:
                return "⚠️ Content extraction failed. Google News often blocks full-text extraction tools. Please use the 'Link' button to read the original article."

            return text if text else "Could not extract text content. Site structure might be complex."
        except Exception as e:
            logger.error(f"Error fetching text: {e}")
            return f"Error fetching content: {e}"

    def generate_summary(self, text, model, link=None, force_refresh=False):
        """
        Generates a 3-bullet point summary using the LLM.
        Returns:
            dict: { 'text': str, 'meta': dict }
        """
        import time
        
        # 1. Check Cache if link provided AND NOT forced
        if link and not force_refresh:
            db = NewsDatabase()
            cached_data = db.get_summary_from_cache(link)
            if cached_data:
                # cached_data is { 'summary', 'model', 'created_at' }
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
        
        # Stronger prompt for small models (0.5b)
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
        
        # Append metadata footer to summary for persistence
        # Using markdown italics for "small" feel
        footer = f"\n\n*(⏱ {elapsed}s | {model} | {current_host})*"
        full_summary = summary + footer
        
        # 2. Save Cache if link provided (Update if exists)
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

    def check_ollama_connection(self):
        """Pass-through to LLMManager check."""
        return self.llm_manager.check_connection()

    def get_gpu_info(self):
        """Pass-through to LLMManager GPU info."""
        return self.llm_manager.get_gpu_info()
