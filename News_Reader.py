import streamlit as st
import pandas as pd
from modules.news_manager import NewsFetcher, NewsDatabase
from modules.llm_manager import LLMManager
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

st.title("Text News Reader")

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

def auto_sum_worker(news_items, model, result_queue, stop_event, fetcher_instance):
    """뉴스 텍스트를 가져오고 요약하는 백그라운드 스레드"""
    
    # 완화된 GPU 체크
    pass 
        
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

# 사이드바
with st.sidebar:
    st.header("Settings")
    mode = st.radio("View Mode", ["Live News", "Saved News"])
    
    if mode == "Live News":
        # 소스 선택
        config = llm_manager.get_config()
        default_source = config.get("default_source")
        source_options = list(fetcher.sources.keys())
        source_index = source_options.index(default_source) if default_source in source_options else 0

        def on_source_change():
             llm_manager.update_config("default_source", st.session_state.current_source_selection)

        source = st.selectbox(
            "Select Source", 
            source_options, 
            index=source_index, 
            key="current_source_selection",
            on_change=on_source_change
        )
        
        # 새로고침 간격
        refresh_options = {
            "Manual": 0,
            "1 Minute": 60,
            "3 Minutes": 180,
            "5 Minutes": 300,
            "10 Minutes": 600
        }
        refresh_label = st.selectbox(
            "Refresh Interval",
            list(refresh_options.keys()),
            index=3, # 기본 5분
            key="refresh_interval_label"
        )
        refresh_interval = refresh_options[refresh_label]
        
        st.markdown("---")
        st.caption("AI Configuration")
        
        # 자동 요약 토글
        if 'auto_summary_enabled' not in st.session_state:
            st.session_state.auto_summary_enabled = config.get("auto_summary_enabled", False)

        def on_summary_toggle():
             llm_manager.update_config("auto_summary_enabled", st.session_state.auto_summary_enabled)

        st.toggle("Auto Summary", key="auto_summary_enabled", on_change=on_summary_toggle)

        # 서버 선택
        server_options = ["remote", "local"]
        current_host_type = llm_manager.selected_host_type
        host_index = server_options.index(current_host_type) if current_host_type in server_options else 0
        
        selected_server_label = st.radio(
            "LLM Server",
            server_options,
            index=host_index,
            format_func=lambda x: "Remote (2080ti)" if x == "remote" else "Local (Docker)",
            key="selected_server_type",
            disabled=not st.session_state.auto_summary_enabled
        )
        
        if selected_server_label != current_host_type:
             llm_manager.set_host_type(selected_server_label)
             st.toast(f"Switched server to {selected_server_label}")
             st.session_state.available_models = llm_manager.get_models()
             st.rerun()

        # 모델 선택
        if 'available_models' not in st.session_state:
             st.session_state.available_models = llm_manager.get_models()
        
        if st.session_state.available_models:
            default_model = llm_manager.get_context_default_model()
            default_index = 0
            if default_model and default_model in st.session_state.available_models:
                default_index = st.session_state.available_models.index(default_model)

            def on_model_change():
                llm_manager.set_context_default_model(st.session_state.selected_model)

            selected_model = st.selectbox(
                "AI Model", 
                st.session_state.available_models, 
                index=default_index,
                key="selected_model",
                on_change=on_model_change
            )
            
            if 'result_queue' not in st.session_state:
                st.session_state.result_queue = queue.Queue()
        else:
            st.warning("AI Models: Not Connected")
            st.caption(f"Host: {llm_manager.current_host}")
            if st.button("Retry Connection"):
                st.session_state.available_models = llm_manager.get_models()
                st.rerun()
            st.session_state.selected_model = None

    st.markdown("---")
    st.caption("**AI Server Status**")
    col_stat1, col_stat2 = st.columns([1,1])
    with col_stat1:
        if st.button("Check", key="check_ollama", use_container_width=True):
            st.session_state.available_models = llm_manager.get_models()
            success, msg = llm_manager.check_connection()
            if success:
                st.toast(f"Connected! Found {len(st.session_state.available_models)} models.")
            else:
                st.toast(msg)
    
    with col_stat2:
        st.write("") 

    st.caption(f"**Host:** {llm_manager.current_host}")

    gpu_info = llm_manager.get_gpu_info()
    if gpu_info:
        count = len(gpu_info)
        names = set(gpu_info)
        name_str = ", ".join(names)
        st.caption(f"**GPU:** {count} Cards Detected ({name_str})")
    else:
        st.caption("**GPU:** Not Detected (SSH Failed)")

    st.markdown("---")
    st.caption("**Server Data Usage (Today)**")
    
    from modules.metrics_manager import DataUsageTracker
    tracker = DataUsageTracker()
    stats = tracker.get_stats()
    
    def format_bytes(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:,.0f} {unit}" if unit == 'B' else f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"

    rx_str = format_bytes(stats['rx_bytes'])
    tx_str = format_bytes(stats['tx_bytes'])
    total_str = format_bytes(stats['total_bytes'])

    st.markdown(f"""
    <div style="font_size: 0.8rem; color: #666;">
        <div style="display: flex; justify-content: space-between;">
            <span>Rx: <b>{rx_str}</b></span>
            <span>Tx: <b>{tx_str}</b></span>
        </div>
        <div style="margin-top: 4px; font-weight: bold;">
            Total: {total_str}
        </div>
    </div>
    """, unsafe_allow_html=True)

# 메인 콘텐츠
if mode == "Live News":
    
    # 새로고침 로직
    should_refresh = False
    
    # 수동 새로고침 버튼 (메인 영역)
    if st.button("🔄 Refresh Feed"):
        should_refresh = True
        
    # 자동 새로고침 타이머
    if refresh_interval > 0:
        if 'last_update' in st.session_state:
            elapsed = time.time() - st.session_state.last_update
            if elapsed >= refresh_interval:
                should_refresh = True
        else:
            should_refresh = True

    st.header(f"Live Feed: {source}")
    
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
                    c_spacer, c_summary = st.columns([0.015, 0.985])
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
                    
                    col_regen, col_save_area = st.columns([1, 3])
                    with col_regen:
                         if st.button("Regen", key=f"sum_{i}"):
                            with st.spinner(f"Asking Local LLM..."):
                                model_to_use = st.session_state.get('selected_model')
                                if model_to_use:
                                    summary_data = fetcher.generate_summary(full_text, model=model_to_use, link=item['link'], force_refresh=True)
                                    st.session_state.summaries[item['link']] = summary_data
                                    st.rerun()
                                else:
                                    st.error("No AI Model.")

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
