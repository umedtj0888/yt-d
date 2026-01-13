from flask import Flask, request, Response, send_file, abort
import os
import json
import logging
import re
import tempfile
import zipfile
import uuid
import yt_dlp

app = Flask(__name__)

# === КОНФИГУРАЦИЯ ===
DOWNLOAD_FOLDER = 'downloads'
PORT = 5000
COOKIES_FILE = 'cookies.txt'

# Создаем папку
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def get_video_id(url):
    """Извлекает ID видео"""
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def clean_text(text):
    """Очищает текст от тегов и мусора"""
    if not text:
        return ""
    
    # Удаляем XML/VTT теги вида <tag>content</tag>
    text = re.sub(r'<[^>]+>', '', text)
    
    # Разбиваем на строки и фильтруем
    lines = text.split('\n')
    clean_lines = []
    
    for line in lines:
        line = line.strip()
        # Пропускаем временные метки (00:00:00 --> 00:00:05)
        if '-->' in line:
            continue
        # Пропускаем служебные строки (WEBVTT, цифры)
        if not line or line.isdigit() or line.startswith('NOTE') or line.startswith('Style'):
            continue
        clean_lines.append(line)
    
    return ' '.join(clean_lines).strip()

def process_subtitles_from_memory(subs):
    """
    Принимает словарь субтитров от yt-dlp, выбирает лучший вариант
    и возвращает чистый текст.
    """
    if not subs:
        return None
        
    # Приоритет: 1. Субтитры вручную (обычно лучше качество), 2. Автоматические
    target_lang = 'en'
    
    best_subs = None
    
    # 1. Ищем ручные субтитры (не auto-generated)
    if target_lang in subs:
        # Сортируем, чтобы взять лучший формат (например, json3 или vtt)
        # Но нам важнее наличие url, чем формат, так как мы будем качать
        # Берем первый доступный
        for sub in subs[target_lang]:
            if 'url' in sub:
                best_subs = sub['url']
                logger.info(f"Found manual subtitles: {target_lang}")
                break
    
    # 2. Если ручных нет, ищем автоматические
    if not best_subs:
        auto_key = f'{target_lang}-auto'
        # YouTube API yt-dlp может называть их как 'en-auto' или возвращать в списке automatic_captions
        # Однако в словаре subtitles ключи просто 'en'. 
        # Чтобы получить точно авто-субтитры, лучше использовать опцию writeautomaticsub=True
        # Но здесь мы парсим уже полученный info_dict.
        
        # Попытка найти auto-субтитры через информацию об автоматических субтитрах, если они переданы отдельно
        # Но обычно yt-dlp сам мержит их в subtitles, если include_manual=False не стоит.
        # Проще: если нет ручных, yt-dlp все равно может вернуть авто-субтитры, если их скачать.
        
        if not best_subs:
            logger.warning("No manual subtitles found, might need auto-subs logic or download attempt.")
            return None

    if not best_subs:
        return None

    # Скачиваем содержимое по найденному URL
    import urllib.request
    try:
        req = urllib.request.Request(best_subs, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8')
            
            # Конвертируем в текст
            text = clean_text(content)
            if len(text) > 50:
                return text
    except Exception as e:
        logger.error(f"Failed to download subtitle content: {e}")
        
    return None

def get_subtitles_via_download(ydl, video_url):
    """
    Метод на случай, если не удалось получить прямую ссылку (URL).
    Принудительно скачивает файл в память.
    """
    # Создаем временный файл
    fd, path = tempfile.mkstemp(suffix='.vtt')
    try:
        # Настраиваем yt-dlp на запись в этот конкретный файл
        opts = {
            'quiet': True,
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True, # Включаем авто-субтитры обязательно
            'subtitleslangs': ['en'],
            'subtitlesformat': 'vtt',
            'outtmpl': path, # Пишем прямо в наш временный файл
            'overwrite': True
        }
        
        with yt_dlp.YoutubeDL(opts) as ydl_down:
            ydl_down.download([video_url])
            
        # Читаем файл
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return clean_text(content)
    finally:
        # Обязательно удаляем временный файл
        try:
            os.close(fd)
            os.remove(path)
        except:
            pass
    return None

# === ОСНОВНАЯ ЛОГИКА ===

def process_video(video_id):
    logger.info(f"Processing: {video_id}")
    
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        # Не указываем skip_download, чтобы получить полный список субтитров
        # Но мы не будем качать видео.
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        logger.info("Using cookies.")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. Извлекаем информацию
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            title = info.get('title', 'Unknown Video')
            
            # 2. Пытаемся получить субтитры напрямую из словаря
            subtitles = info.get('subtitles')
            automatic_captions = info.get('automatic_captions')
            
            text = None
            
            # Сначала пробуем обычные
            if subtitles:
                text = process_subtitles_from_memory(subtitles)
            
            # Если не вышло, пробуем автоматические
            if not text and automatic_captions:
                # Для автоматических структура такая же
                text = process_subtitles_from_memory(automatic_captions)
            
            # 3. Если через URL не вышло (иногда YouTube требует токен), используем метод скачивания
            if not text:
                logger.info("Direct URL method failed, trying download method...")
                text = get_subtitles_via_download(ydl, url)
            
            if text and len(text) > 50:
                return {
                    'title': title,
                    'text': text,
                    'video_id': video_id
                }
            
            return None

    except Exception as e:
        logger.error(f"Critical Error: {e}")
        return None

def create_zip(title, text, video_id):
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:50] or "subtitles"
    zip_name = f"{video_id}_{uuid.uuid4().hex[:6]}.zip"
    zip_path = os.path.join(DOWNLOAD_FOLDER, zip_name)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        content_str = f"Title: {title}\nVideo ID: {video_id}\n\n{text}"
        zf.writestr(f"{safe_title}.txt", content_str.encode('utf-8'))
        
    return zip_name

# === ROUTES ===

@app.route('/')
def index():
    cookies_status = "Active" if os.path.exists(COOKIES_FILE) else "Inactive"
    return f"<h1>Subtitle Service</h1>Status: {cookies_status}<br>Usage: /download?url=VIDEO_URL"

@app.route('/download')
def download_route():
    url = request.args.get('url')
    
    if not url:
        return Response(json.dumps({"error": "Missing URL"}), status=400)
    
    video_id = get_video_id(url)
    if not video_id:
        return Response(json.dumps({"error": "Invalid YouTube URL"}), status=400)
    
    result = process_video(info.get('subtitles')
            if not text and automatic_captions:
                text = process_subtitles_from_memory(automatic_captions)
            
            # 3. Если через URL не вышло, скачиваем файл принудительно
            if not text:
                logger.info("Direct URL method failed, trying download method...")
                text = get_subtitles_via_download(ydl, url)
            
            if text and len(text) > 50:
                return {
                    'title': title,
                    'text': text,
                    'video_id': error processing video {video_id}: {e}")
    return None

def create_zip(title, text, video_id):
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:50] or "subtitles"
    zip_name = f"{video_id}_{uuid.uuid4().hex[:6]}.zip"
    zip_path = os.path.join(DOWNLOAD_FOLDER, zip_name)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        content_str = f"Title: {title}\nVideo ID: {video_id}\n\n{text}"
        zf.writestr(f"{safe_title}.txt", content_str.encode('utf-8'))
        
    return zip_name

# === ROUTES ===

@app.route('/')
def index():
    cookies_status = "Active" if os.path.exists(COOKIES_FILE) else "Inactive"
    return f"<h1>Subtitle Service</h1>Status: {cookies_status}<br>Usage: /download?url=VIDEO_URL"

@app.route('/download')
def download_route():
   _video_id)
    
    if not result:
        return Response(json.dumps({"error": "Subtitles not found or download failed. Try another video."}), status=404)
    
    zip_filename = create_zip(result['title'], result['text'], result['video_id'])
    
    base_url = request.host_url.rstrip('/')
    return Response(json.dumps({
        "success": True,
        "title": result['title'],
        "download_url": f"{base_url}/file/{zip_filename}",
        "video_id": result['video_id']
    }), mimetype='application/json')

@app.route('/file/<filename>')
def file_download(filename):
    path = os.path.join(COOKIES_FILE)
        ydl_opts['cookiefile'] = COOKIES_FILE
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. Извлекаем информацию
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            title = info.get('title', 'Unknown Video')
            
            # 2. Пытаемся получить субтитры напрямую из словаря
            subtitles = info.get('subtitles')
            automatic_captions = info.get('automatic_captions')
            
            text = None
            
            if subtitles:
                text = process_subtitles_from_memory(subtitles)
            
            if not text and automatic_captions:
                text = process_subtitles_from_memory(automatic_captions)
            
            # 3. Если через URL не вышло, скачиваем файл принудительно
            if not text:
                logger.info("Direct URL method failed, trying download method...")
                text = get_subtitles_via_download(ydl, url)
            
            if text and len(text) >  YouTube API or video restrictions.")
    return None

# === ROUTES ===

@app.route('/')
def index():
    cookies_status = "Active" if os.path.exists(COOKIES_FILE) else "Inactive"
    return return "<h1>Subtitle Service</h1>Status: {cookies_status}<br>Usage: /download?url=VIDEO_URL"

@app.route('/download')
def download_route():
    _video_id)
    
    if not result:
        return Response(json.dumps({"error": "Subtitles not found or download failed. Try another video or check cookies.txt"}), status=404)
    
    zip_filename = create_zip(result['title'], result['text'], result['video_id'])
    
    base_url = request.host_url.rstrip('/')
    return Response(json.dumps({
        "success": True,
        "title": result['title'],
        "download_url": f"{base_url}/file/{zip_filename}",
        "video_id": result['video_id']
    }), mimetype='application/json')

@app.route('/file/<filename>')
def file_download(filename):
    path = os.path.join(DOWNLOAD_FOLDER, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    logger.info(f"Server starting on port {PORT}")
    if os.path.exists(COOKIES_FILE):
        logger.info("Cookies file detected.")
    else:
        logger.warning("Cookies file NOT detected. Requests might be limited by YouTube.")
    app.run(host='0.0.0.0', port=PORT, debug=True)
