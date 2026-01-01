import os
import time
import re
import uuid
import zipfile
import threading
from flask import Flask, request, Response, send_file, jsonify
import yt_dlp
import urllib.request
import json as json_lib

app = Flask(__name__)

# --- КОНФИГУРАЦИЯ ---
UPLOAD_FOLDER = 'subtitles'
COOKIES_FILE = 'cookies.txt'
MAX_AGE_SECONDS = 3600  # Время жизни файла (1 час)

# Создаем папку если её нет
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Блокировка для потокобезопасности
file_lock = threading.Lock()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def cleanup_old_files():
    """Удаляет старые файлы (старше 1 часа). Потокобезопасно."""
    try:
        with file_lock:
            now = time.time()
            for filename in os.listdir(UPLOAD_FOLDER):
                if filename.endswith('.zip'):
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    try:
                        if os.path.getmtime(filepath) < now - MAX_AGE_SECONDS:
                            os.remove(filepath)
                            print(f"🧹 Удален старый файл: {filename}")
                    except OSError as e:
                        print(f"⚠️ Ошибка удаления файла {filename}: {e}")
    except Exception as e:
        print(f"❌ Ошибка в cleanup_old_files: {e}")

def extract_video_id(youtube_url):
    """Извлекает ID видео из URL"""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtube\.com\/embed\/([^\/\?]+)',
        r'youtu\.be\/([^\?]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    return None

def get_video_info(video_id):
    """Получает информацию о видео через oembed API"""
    try:
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json_lib.loads(response.read().decode('utf-8'))
            return {
                'title': data.get('title', 'Unknown Video'),
                'author_name': data.get('author_name', 'Unknown Author')
            }
    except Exception as e:
        print(f"⚠️ Не удалось получить oembed для {video_id}: {e}")
        return {'title': 'Unknown Video', 'author_name': 'Unknown Author'}

def srt_to_text(srt_content):
    """Конвертирует SRT формат в чистый текст"""
    lines = srt_content.split('\n')
    text_lines = []
    
    html_tag_pattern = re.compile('<.*?>')
    
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or '-->' in line:
            continue
        
        line = re.sub(html_tag_pattern, '', line)
        
        if line and line not in ['[Music]', '[Music] ', '(Music)']:
            text_lines.append(line)
    
    text = ' '.join(text_lines)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def create_zip_file(video_title, subtitles_text, video_id):
    """Создает ZIP файл с субтитрами. Потокобезопасно."""
    clean_title = re.sub(r'[<>:"/\\|?*]', '_', video_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    if len(clean_title) > 50:
        clean_title = clean_title[:50]
    
    zip_filename = f"{video_id}_{uuid.uuid4().hex[:6]}.zip"
    zip_filepath = os.path.join(UPLOAD_FOLDER, zip_filename)
    
    with file_lock:
        with zipfile.ZipFile(zip_filepath, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            content = f"{video_title}\n\n{subtitles_text}"
            zipf.writestr(f"{clean_title}.txt", content.encode('utf-8'))
    
    return zip_filename, clean_title

def download_subtitles_logic(video_id):
    """Логика скачивания через yt_dlp"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'subtitlesformat': 'srt',
        'socket_timeout': 20,
        'retries': 3,
        'nooverwrites': True,
        'noplaylist': True,
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            ydl_opts['outtmpl'] = os.path.join(temp_dir, 'subtitle.%(ext)s')
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            
            srt_files = [f for f in os.listdir(temp_dir) if f.endswith('.srt')]
            
            if not srt_files:
                return None
                
            srt_path = os.path.join(temp_dir, srt_files[0])
            
            with open(srt_path, 'r', encoding='utf-8-sig') as f:
                srt_content = f.read()
            
            subtitles_text = srt_to_text(srt_content)
            
            if not subtitles_text:
                return None
                
            video_info = get_video_info(video_id)
            
            return {
                'title': video_info['title'],
                'author': video_info['author_name'],
                'subtitles': subtitles_text,
                'video_id': video_id
            }
            
    except Exception as e:
        print(f"❌ Ошибка yt_dlp: {e}")
        return None

# --- ROUTES ---

@app.route('/')
def home():
    cleanup_old_files()
    cookies_status = "✅ Найден" if os.path.exists(COOKIES_FILE) else "❌ Не найден"
    
    # Генерируем пример ссылки
    example_url = f"{request.host_url}download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    
    return f"""
    <h1>🚀 YouTube Subtitles Downloader (GET Version)</h1>
    <p>Status: <b>Online</b></p>
    <p>Cookies.txt: {cookies_status}</p>
    <p>Отправьте GET запрос на /download с параметром url:</p>
    <a href="{example_url}" target="_blank">
        <button style="padding:10px; font-size:16px; cursor:pointer;">🧪 Тестировать (Rick Roll)</button>
    </a>
    <p>Или вставьте в адресную строку:</p>
    <pre>{example_url}</pre>
    """

@app.route('/download') # Убрали methods=['POST'], по умолчанию GET
def download_subtitles_route():
    """Эндпоинт для скачивания через GET"""
    cleanup_old_files()
    
    # Получаем параметр 'url' из строки запроса (?url=...)
    youtube_url = request.args.get('url')
    
    if not youtube_url:
        # Если URL нет, возвращаем ошибку в виде JSON (браузер может показать его как текст)
        return jsonify({'success': False, 'error': 'Параметр url обязателен. Пример: /download?url=https://...'}), 400
    
    youtube_url = youtube_url.strip()
    video_id = extract_video_id(youtube_url)
    
    if not video_id:
        return jsonify({'success': False, 'error': 'Неверный YouTube URL'}), 400
    
    print(f"📥 GET Запрос субтитров: {video_id}")
    
    result = download_subtitles_logic(video_id)
    
    if not result or not result.get('subtitles'):
        return jsonify({'success': False, 'error': 'Не удалось найти или скачать субтитры'}), 500
    
    try:
        zip_filename, clean_title = create_zip_file(result['title'], result['subtitles'], video_id)
        
        response_data = {
            'success': True,
            'video_title': result['title'],
            'author': result['author'],
            'video_id': video_id,
            'download_url': f"{request.host_url}download/{zip_filename}",
            'filename': f"{clean_title}.zip",
            'cookies_used': os.path.exists(COOKIES_FILE)
        }
        return jsonify(response_data)
        
    except Exception as e:
        print(f"❌ Ошибка создания архива: {e}")
        return jsonify({'success': False, 'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/download/<filename>')
def download_file(filename):
    """Отдача файла пользователю"""
    if '..' in filename or '/' in filename:
        return jsonify({'success': False, 'error': 'Некорректное имя файла'}), 400
        
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': 'Файл не найден или устарел'}), 404
    
    try:
        print(f"📤 Отдача файла: {filename}")
        return send_file(
            filepath,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"❌ Ошибка отдачи файла: {e}")
        return jsonify({'success': False, 'error': 'Ошибка скачивания'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
