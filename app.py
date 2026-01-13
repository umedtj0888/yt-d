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
COOKIES_FILE = 'cookies.txt'  # Имя файла с cookies

# Создаем папку для скачивания
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def get_video_id(url):
    """Извлекает ID видео из YouTube ссылки"""
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def clean_subtitles(content):
    """Очищает содержимое файла от временных меток и тегов, оставляя только текст"""
    lines = content.split('\n')
    text_lines = []
    
    for line in lines:
        line = line.strip()
        # Пропускаем пустые строки, индексы и временные метки
        if not line or line.isdigit() or '-->' in line:
            continue
        # Удаляем HTML теги и добавляем текст
        clean_line = re.sub(r'<[^>]+>', '', line)
        if clean_line:
            text_lines.append(clean_line)
            
    return ' '.join(text_lines)

def create_zip(title, text, video_id):
    """Создает ZIP архив с текстовым файлом"""
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:50] or "subtitles"
    zip_name = f"{video_id}_{uuid.uuid4().hex[:6]}.zip"
    zip_path = os.path.join(DOWNLOAD_FOLDER, zip_name)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        content_str = f"Title: {title}\nVideo ID: {video_id}\n\n{text}"
        zf.writestr(f"{safe_title}.txt", content_str.encode('utf-8'))
        
    return zip_name

# === ОСНОВНАЯ ЛОГИКА СКАЧИВАНИЯ ===

def process_video(video_id):
    """
    Скачивает информацию и субтитры, используя cookies если они доступны.
    """
    logger.info(f"Processing video ID: {video_id}")
    
    # Базовые настройки yt-dlp
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,          # Не качать видео
        'writesubtitles': True,         # Обычные субтитры
        'writeautomaticsub': True,      # Автоматические (самый надежный метод)
        'subtitleslangs': ['en'],       # Язык
        'subtitlesformat': 'vtt',
        'outtmpl': os.path.join(tempfile.gettempdir(), '%(id)s.%(ext)s'),
        # Добавляем User-Agent, чтобы выглядеть как обычный браузер
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    }
    
    # Проверяем наличие cookies.txt и добавляем в настройки
    if os.path.exists(COOKIES_FILE):
        try:
            # Проверяем, что файл не пустой
            if os.path.getsize(COOKIES_FILE) > 0:
                ydl_opts['cookiefile'] = COOKIES_FILE
                logger.info("🍪 Cookies file loaded. Using cookies for requests.")
        except Exception as e:
            logger.warning(f"Could not load cookies: {e}")
    else:
        logger.info("⚠️ No cookies.txt found. Proceeding without cookies (may be slower or rate-limited).")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Извлекаем информацию
            url = f"https://www.youtube.com/watch?v={video_id}"
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
                
            title = info.get('title', 'Unknown Video')
            
            # Скачиваем только субтитры
            ydl.download([url])
            
            # Ищем скачанный файл
            # Временная папка системы + ID видео + язык + формат
            potential_files = [
                os.path.join(tempfile.gettempdir(), f"{video_id}.en.vtt"),
                os.path.join(tempfile.gettempdir(), f"{video_id}.en.vtt.tmp") # Иногда yt-dlp оставляет .tmp
            ]
            
            sub_content = None
            for filepath in potential_files:
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        sub_content = f.read()
                    # Удаляем временный файл сразу
                    try:
                        os.remove(filepath)
                    except:
                        pass
                    break
            
            if not sub_content:
                logger.warning(f"Subtitles file not found in temp dir for {video_id}")
                return None
                
            # Очищаем
            clean_text = clean_subtitles(sub_content)
            
            if len(clean_text) < 50:
                return None
                
            return {
                'title': title,
                'text': clean_text,
                'video_id': video_id
            }

    except Exception as e:
        logger.error(f"Error processing video {video_id}: {e}")
        return None

# === FLASK ROUTES ===

@app.route('/')
def index():
    cookies_status = "✅ Подключен" if os.path.exists(COOKIES_FILE) else "❌ Не найден (cookies.txt)"
    return f"""
    <h1>Subtitle Downloader (Reliable)</h1>
    <p>Status: {cookies_status}</p>
    <p>Используйте GET запрос: <code>/download?url=YOUR_URL</code></p>
    """

@app.route('/download')
def download_route():
    url = request.args.get('url')
    
    if not url:
        return Response(json.dumps({"error": "Missing URL parameter"}), status=400)
    
    video_id = get_video_id(url)
    if not video_id:
        return Response(json.dumps({"error": "Invalid YouTube URL"}), status=400)
    
    result = process_video(video_id)
    
    if not result:
        return Response(json.dumps({"error": "Subtitles not found or download failed"}), status=404)
    
    zip_filename = create_zip(result['title'], result['text'], result['video_id'])
    
    base_url = request.host_url.rstrip('/')
    response_data = {
        "success": True,
        "title": result['title'],
        "video_id": result['video_id'],
        "download_url": f"{base_url}/file/{zip_filename}",
        "text_length": len(result['text'])
    }
    
    return Response(json.dumps(response_data), mimetype='application/json')

@app.route('/file/<filename>')
def file_download(filename):
    """Отдача ZIP файла"""
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
