import streamlit as st

from modules.news_manager import NewsFetcher, NewsDatabase
from modules.llm_manager import LLMManager
from modules.workers import auto_sum_worker
from modules.ui_components import render_sidebar
import time
import queue
import threading

# 페이지 설정
st.set_page_config(page_title="News Reader", page_icon=None, layout="wide")

# 버튼을 왼쪽 정렬하고 모바일 헤더를 조정하기 위한 CSS
st.markdown("""
<style>
div[data-testid="stMainBlockContainer"] .stButton button {
    justify-content: flex-start !important;
    text-align: left !important;
    font-size: 14px !important;
    padding-top: 0.25rem !important;
    padding-bottom: 0.25rem !important;
    line-height: 1.4 !important;
}
h1 { font-size: 1.8rem !important; }
h2 { font-size: 1.5rem !important; }
</style>
""", unsafe_allow_html=True)



# 매니저 초기화 (세션 상태에 유지)
if 'llm_manager' not in st.session_state:
    st.session_state.llm_manager = LLMManager()
if 'fetcher' not in st.session_state:
    st.session_state.fetcher = NewsFetcher()
if 'db' not in st.session_state:
    st.session_state.db = NewsDatabase()

llm_manager = st.session_state.llm_manager
fetcher = st.session_state.fetcher
db = st.session_state.db



# 사이드바
# 사이드바 렌더링 및 설정 가져오기
mode, refresh_interval, config = render_sidebar(llm_manager, fetcher)

# 타이틀 및 상단 버튼 (메인 영역)
st.title("Text News Reader")

# 메인 콘텐츠
if mode == "Live News":
    
    # 소스 선택 및 새로고침 버튼
    # config는 sidebar 상단에서 이미 로드됨
    default_source = config.get("default_source")
    source_options = list(fetcher.sources.keys())
    source_index = source_options.index(default_source) if default_source in source_options else 0

    def on_source_change():
            llm_manager.update_config("default_source", st.session_state.current_source_selection)
    
    col_sel, col_btn = st.columns([0.9, 0.1])
    with col_sel:
        source = st.selectbox(
            "Select Source", 
            source_options, 
            index=source_index, 
            key="current_source_selection",
            on_change=on_source_change,
            label_visibility="collapsed",
        )
    with col_btn:
        manual_refresh = False
        if st.button("🔄", key="main_refresh_btn", help="Fetch new feed"):
            manual_refresh = True

    # 새로고침 로직
    should_refresh = manual_refresh
        
    # 자동 새로고침 타이머
    if refresh_interval > 0:
        if 'last_update' in st.session_state:
            elapsed = time.time() - st.session_state.last_update
            if elapsed >= refresh_interval:
                should_refresh = True
        else:
            should_refresh = True
    
    if should_refresh or 'current_source' not in st.session_state or st.session_state.current_source != source:
        with st.spinner("Fetching news feed..."):
            new_items = fetcher.fetch_feeds(source)
            
            if new_items is None:
                st.toast("No new articles found.")
                st.session_state.last_update = time.time() # 변경 사항이 없어도 타이머 재설정
            else:
                st.session_state.news_items = new_items
                st.session_state.current_source = source
                st.session_state.last_update = time.time()
                if 'stop_event' in st.session_state:
                    st.session_state.stop_event.set()
                    
                # DB에서 요약 미리 가져오기
                st.session_state.summaries = {}
                for item in st.session_state.news_items:
                    cached = db.get_summary_from_cache(item['link'])
                    if cached:
                        formatted_cached = {
                            'text': cached['summary'],
                            'meta': {
                                'source': 'Cache',
                                'time': 'N/A',
                                'host': 'DB',
                                'model': cached.get('model', 'Unknown')
                            }
                        }
                        st.session_state.summaries[item['link']] = formatted_cached

    if not st.session_state.news_items:
        st.info("No news items found or unable to fetch.")
    
    # 스레드 관리
    auto_sum_on = st.session_state.get('auto_summary_enabled', False)
    selected_model = st.session_state.get('selected_model')
    
    if auto_sum_on and selected_model:
        need_start = False
        if 'auto_thread' not in st.session_state:
            need_start = True
        elif not st.session_state.auto_thread.is_alive():
            need_start = True
        elif st.session_state.get('stop_event') and st.session_state.stop_event.is_set():
             need_start = True
        
        if need_start:
             if 'summaries' not in st.session_state: st.session_state.summaries = {}
             items_to_process = [i for i in st.session_state.news_items if i['link'] not in st.session_state.summaries]
             if items_to_process:
                 stop_event = threading.Event()
                 t = threading.Thread(
                     target=auto_sum_worker, 
                     args=(items_to_process, selected_model, st.session_state.result_queue, stop_event, fetcher),
                     daemon=True
                 )
                 t.start()
                 st.session_state.auto_thread = t
                 st.session_state.stop_event = stop_event
    else:
        if 'stop_event' in st.session_state:
            st.session_state.stop_event.set()
    
    if 'fetched_texts' not in st.session_state:
        st.session_state.fetched_texts = {}

    @st.fragment(run_every=2)
    def render_news_list():
        if 'summaries' not in st.session_state:
            st.session_state.summaries = {}

        if 'result_queue' in st.session_state:
            try:
                while True:
                    link, summary_data = st.session_state.result_queue.get_nowait()
                    st.session_state.summaries[link] = summary_data
                    # 전체 텍스트가 반환되면 캐시
                    if 'full_text' in summary_data and summary_data['full_text']:
                        st.session_state.fetched_texts[link] = summary_data['full_text']
            except queue.Empty:
                pass
        
        if 'last_update' in st.session_state and refresh_interval > 0:
            elapsed = time.time() - st.session_state.last_update
            remaining = max(0, refresh_interval - int(elapsed))
            st.caption(f"⏳ Refresh in: {remaining//60:02d}:{remaining%60:02d}")
            if elapsed >= refresh_interval:
                 st.rerun() 
            
        for i, item in enumerate(st.session_state.news_items):
            with st.container():
                if st.button(f"{item['title']}", key=f"title_btn_{i}", use_container_width=True):
                    if st.session_state.get('expanded_id') == i:
                        st.session_state.expanded_id = None
                    else:
                         st.session_state.expanded_id = i
                         if item['link'] not in st.session_state.fetched_texts:
                             with st.spinner("Fetching full text..."):
                                 text = fetcher.get_full_text(item['link'])
                                 st.session_state.fetched_texts[item['link']] = text
                    st.rerun()

                # Show Summary
                if item['link'] in st.session_state.summaries:
                    c_btn, c_summary = st.columns([0.08, 0.92])
                    with c_btn:
                         st.write("") # Vertical alignment spacer
                         if st.button("🔄", key=f"regen_btn_{i}", help="Regenerate Summary"):
                             with st.spinner("..."):
                                 link = item['link']
                                 # 텍스트가 메모리에 없으면 가져오기
                                 if link not in st.session_state.fetched_texts:
                                     st.session_state.fetched_texts[link] = fetcher.get_full_text(link)
                                 
                                 full_text = st.session_state.fetched_texts[link]
                                 model_to_use = st.session_state.get('selected_model')
                                 
                                 if model_to_use:
                                    summary_data = fetcher.generate_summary(full_text, model=model_to_use, link=link, force_refresh=True)
                                    st.session_state.summaries[link] = summary_data
                                    st.rerun()
                                 else:
                                    st.error("No Model")

                    with c_summary:
                        data = st.session_state.summaries[item['link']]
                        if isinstance(data, dict):
                            text_content = data.get('text') or data.get('summary') or "Error: No text"
                            st.info(text_content)
                        else:
                            st.info(data)

                st.caption(f"Published: {item['published']}")
                
                if st.session_state.get('expanded_id') == i:
                    st.markdown("---")
                    full_text = st.session_state.fetched_texts.get(item['link'], "")
                    
                    # Regen 버튼이 있던 자리는 제거하고 저장 버튼만 남김
                    col_save_area = st.container()
                    with col_save_area:
                        c_comment, c_btn = st.columns([4, 1])
                        with c_comment:
                            user_comment = st.text_input("Note", key=f"comment_{i}", placeholder="Comment...", label_visibility="collapsed")
                        with c_btn:
                            if st.button("Save", key=f"save_{i}"):
                                sum_val = st.session_state.summaries.get(item['link'], "")
                                sum_text = sum_val['text'] if isinstance(sum_val, dict) else sum_val
                                
                                article_data = {
                                    'title': item['title'],
                                    'link': item['link'],
                                    'published': item['published'],
                                    'source': item['source'],
                                    'summary': sum_text,
                                    'content': full_text,
                                    'comment': user_comment
                                }
                                if db.save_article(article_data):
                                    st.toast("Saved to DB!")
                                else:
                                    st.error("Save failed.")
    
                    st.write(full_text)
                    st.markdown(f"[Original Link]({item['link']})")
                    st.markdown("---")
                    st.empty() 
    
    render_news_list()

elif mode == "Saved News":
    st.header("Saved Articles")
    saved_items = db.get_saved_articles()
    
    if not saved_items:
        st.info("No saved articles found.")
    else:
        for item in saved_items:
             with st.expander(f"{item['title']} (Saved: {item['created_at']})"):
                 st.markdown(f"**Source:** {item['source']}")
                 if item.get('comment'):
                     st.warning(f"**Note:** {item['comment']}")
                 st.markdown("**Summary:**")
                 st.info(item['summary'])
                 st.markdown("**Full Text:**")
                 st.text(item['content'])
                 st.markdown(f"[Original Link]({item['link']})")
