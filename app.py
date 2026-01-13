# app.py
from flask import Flask, request, Response, send_file, abort
import json
import os
import time
import re
import tempfile
import uuid
import zipfile
import yt_dlp
import logging
from functools import wraps
from werkzeug.utils import secure_filename
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.error

app = Flask(__name__)
UPLOAD_FOLDER = 'subtitles'
COOKIES_FILE = 'cookies.txt'

# Конфигурация
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024  # 16KB для GET параметров
MAX_SUBTITLES_SIZE = 10 * 1024 * 1024  # 10MB максимальный размер субтитров
CLEANUP_AGE = 3600  # 1 час

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Создаем папки если их нет
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def is_valid_youtube_url(url):
    """Проверяет валидность YouTube URL"""
    parsed = urlparse(url)
    
    # Разрешенные домены YouTube
    allowed_domains = [
        'youtube.com',
        'www.youtube.com',
        'm.youtube.com',
        'youtu.be',
        'www.youtu.be'
    ]
    
    if parsed.netloc not in allowed_domains:
        return False
    
    # Проверяем наличие пути к видео
    path = parsed.path.lower()
    if '/watch' not in path and '/embed/' not in path and parsed.netloc == 'youtube.com':
        return False
    
    return True

def extract_video_id(youtube_url):
    """Извлекает ID видео из URL с валидацией"""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11})',
        r'youtube\.com\/embed\/([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            video_id = match.group(1)
            # Дополнительная валидация ID YouTube
            if re.match(r'^[0-9A-Za-z_-]{11}$', video_id):
                return video_id
    return None

def is_valid_video_id(video_id):
    """Проверяет валидность ID видео"""
    return bool(re.match(r'^[0-9A-Za-z_-]{11}$', video_id))

def get_video_info(video_id):
    """Безопасно получает информацию о видео"""
    try:
        if not is_valid_video_id(video_id):
            return None
            
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # Устанавливаем лимиты
        timeout = 10
        max_size = 1024 * 1024  # 1MB
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # Проверяем размер ответа
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > max_size:
                raise ValueError("Response too large")
            
            data = response.read(max_size).decode('utf-8')
            video_info = json.loads(data)
            
            return {
                'title': video_info.get('title', 'Unknown Video')[:500],  # Ограничиваем длину
                'author_name': video_info.get('author_name', 'Unknown Author')[:200]
            }
            
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to get video info: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting video info: {e}")
        return None

def download_subtitles(video_id):
    """Безопасно скачивает субтитры с YouTube"""
    if not is_valid_video_id(video_id):
        return None
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'subtitlesformat': 'srt',
        'socket_timeout': 15,
        'retries': 1,
        'nooverwrites': True,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android'],
                'skip': ['hls', 'dash']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    }
    
    # Безопасная загрузка cookies
    if os.path.exists(COOKIES_FILE):
        try:
            # Проверяем размер файла cookies
            if os.path.getsize(COOKIES_FILE) > 1024 * 1024:  # 1MB
                logger.warning("Cookies file too large, skipping")
            else:
                ydl_opts['cookiefile'] = COOKIES_FILE
                logger.info("Using cookies.txt")
        except OSError as e:
            logger.warning(f"Cannot read cookies file: {e}")
    
    try:
        with tempfile.TemporaryDirectory(prefix='yt_subtitles_') as temp_dir:
            ydl_opts['outtmpl'] = os.path.join(temp_dir, 'subtitle.%(ext)s')
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={video_id}"
                ydl.download([url])
            
            # Ищем файл субтитров
            for file in os.listdir(temp_dir):
                if file.endswith('.srt'):
                    filepath = os.path.join(temp_dir, file)
                    
                    # Проверяем размер файла
                    file_size = os.path.getsize(filepath)
                    if file_size > MAX_SUBTITLES_SIZE:
                        logger.warning(f"Subtitles file too large: {file_size}")
                        continue
                    
                    # Читаем с обработкой кодировок
                    for encoding in ['utf-8', 'latin-1', 'cp1252']:
                        try:
                            with open(filepath, 'r', encoding=encoding) as f:
                                srt_content = f.read()
                            
                            # Конвертируем в текст
                            subtitles_text = srt_to_text(srt_content)
                            
                            # Ограничиваем размер текста
                            if len(subtitles_text.encode('utf-8')) > MAX_SUBTITLES_SIZE:
                                subtitles_text = subtitles_text[:MAX_SUBTITLES_SIZE]
                            
                            # Получаем информацию о видео
                            video_info = get_video_info(video_id) or {
                                'title': 'Unknown Video',
                                'author_name': 'Unknown Author'
                            }
                            
                            return {
                                'title': video_info['title'],
                                'author': video_info['author_name'],
                                'subtitles': subtitles_text,
                                'video_id': video_id
                            }
                            
                        except UnicodeDecodeError:
                            continue
                    
            return None
            
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading subtitles: {e}")
        return None

def srt_to_text(srt_content):
    """Конвертирует SRT формат в чистый текст"""
    if not srt_content:
        return ""
    
    lines = srt_content.split('\n')
    text_lines = []
    
    for line in lines:
        line = line.strip()
        # Пропускаем номера строк и временные метки
        if not line or line.isdigit() or '-->' in line:
            continue
        # Убираем HTML теги
        line = re.sub(r'<[^>]+>', '', line)
        text_lines.append(line)
    
    # Объединяем и убираем лишние пробелы
    text = ' '.join(text_lines)
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()[:MAX_SUBTITLES_SIZE]

def create_zip_file(video_title, subtitles_text, video_id):
    """Создает ZIP файл с субтитрами"""
    # Очищаем название для файла
    clean_title = re.sub(r'[<>:"/\\|?*]', '_', video_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    if len(clean_title) > 100:  # Увеличил лимит
        clean_title = clean_title[:100]
    
    if not clean_title:
        clean_title = "subtitles"
    
    # Генерируем уникальное имя файла
    zip_filename = f"{video_id}_{uuid.uuid4().hex[:8]}.zip"
    zip_filepath = os.path.join(UPLOAD_FOLDER, zip_filename)
    
    # Создаем ZIP с безопасным именем файла внутри
    safe_internal_name = secure_filename(f"{clean_title}.txt")
    if not safe_internal_name.endswith('.txt'):
        safe_internal_name += '.txt'
    
    try:
        with zipfile.ZipFile(zip_filepath, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            content = f"{video_title}\n\n{subtitles_text}"
            zipf.writestr(safe_internal_name, content.encode('utf-8'))
        
        return zip_filename, clean_title
        
    except Exception as e:
        logger.error(f"Error creating zip file: {e}")
        # Удаляем частично созданный файл
        if os.path.exists(zip_filepath):
            os.remove(zip_filepath)
        raise

def cleanup_old_files():
    """Удаляем старые файлы"""
    try:
        now = time.time()
        count = 0
        
        for filename in os.listdir(UPLOAD_FOLDER):
            if filename.endswith('.zip'):
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                
                try:
                    file_age = now - os.path.getmtime(filepath)
                    if file_age > CLEANUP_AGE:
                        os.remove(filepath)
                        count += 1
                        logger.info(f"Cleaned up old file: {filename}")
                except (OSError, FileNotFoundError):
                    continue
        
        if count > 0:
            logger.info(f"Cleaned up {count} old files")
            
    except Exception as e:
        logger.error(f"Error in cleanup: {e}")

def validate_filename(filename):
    """Валидирует имя файла для безопасного использования"""
    if not filename or not isinstance(filename, str):
        return False
    
    # Проверяем безопасное имя файла
    if filename != secure_filename(filename):
        return False
    
    # Проверяем формат
    if not re.match(r'^[a-zA-Z0-9_-]{11}_[a-f0-9]{8}\.zip$', filename):
        return False
    
    # Проверяем что файл существует в нашей папке
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(filepath):
        return False
    
    return True

def rate_limit_exempt(f):
    """Декоратор для исключения из rate limiting (для главной страницы)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@rate_limit_exempt
def home():
    """Главная страница с инструкцией"""
    cleanup_old_files()
    
    cookies_status = "✅ Найден" if os.path.exists(COOKIES_FILE) else "❌ Не найден"
    files_count = len([f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.zip')])
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>YouTube Subtitles Downloader</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
            pre {{ background: #f4f4f4; padding: 10px; border-radius: 5px; overflow-x: auto; }}
            .status {{ padding: 10px; border-radius: 5px; margin: 10px 0; }}
            .success {{ background: #d4edda; color: #155724; }}
            .warning {{ background: #fff3cd; color: #856404; }}
            .form {{ margin: 20px 0; }}
            .input {{ width: 100%; padding: 10px; margin: 10px 0; }}
            .button {{ background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }}
        </style>
    </head>
    <body>
        <h1>🚀 YouTube Subtitles Downloader</h1>
        
        <div class="status {'success' if os.path.exists(COOKIES_FILE) else 'warning'}">
            <strong>Статус:</strong> Cookies.txt: {cookies_status} | Файлов в кэше: {files_count}
        </div>
        
        <h2>📥 Скачать субтитры:</h2>
        
        <div class="form">
            <input type="text" id="url" class="input" placeholder="https://youtube.com/watch?v=VIDEO_ID">
            <button onclick="downloadSubtitles()" class="button">Скачать субтитры</button>
        </div>
        
        <div id="result" style="margin: 20px 0;"></div>
        
        <h2>📋 Примеры использования:</h2>
        
        <h3>Через браузер:</h3>
        <pre>
        https://ваш-сервис.onrender.com/download?url=https://youtube.com/watch?v=dQw4w9WgXcQ
        </pre>
        
        <h3>Через curl:</h3>
        <pre>
        curl -X GET "https://ваш-сервис.onrender.com/download?url=https://youtube.com/watch?v=dQw4w9WgXcQ"
        </pre>
        
        <h3>Через JavaScript:</h3>
        <pre>
        fetch('https://ваш-сервис.onrender.com/download?url=' + encodeURIComponent(youtube_url))
            .then(response => response.json())
            .then(data => console.log(data));
        </pre>
        
        <script>
            function downloadSubtitles() {{
                const url = document.getElementById('url').value.trim();
                const resultDiv = document.getElementById('result');
                
                if (!url) {{
                    resultDiv.innerHTML = '<div class="status warning">Введите URL видео</div>';
                    return;
                }}
                
                resultDiv.innerHTML = '<div class="status">⏳ Обработка...</div>';
                
                fetch(`/download?url=${{encodeURIComponent(url)}}`)
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            resultDiv.innerHTML = `
                                <div class="status success">
                                    <strong>✅ Готово!</strong><br>
                                    Видео: ${{data.video_title}}<br>
                                    Автор: ${{data.author}}<br>
                                    <a href="${{data.download_url}}" target="_blank">Скачать ZIP файл</a>
                                </div>
                            `;
                        }} else {{
                            resultDiv.innerHTML = `<div class="status warning">❌ Ошибка: ${{data.error}}</div>`;
                        }}
                    }})
                    .catch(error => {{
                        resultDiv.innerHTML = `<div class="status warning">❌ Ошибка сети: ${{error.message}}</div>`;
                    }});
            }}
        </script>
    </body>
    </html>
    """

@app.route('/download')
def download_subtitles_route():
    """Основной эндпоинт для скачивания субтитров (GET)"""
    cleanup_old_files()
    
    try:
        # Получаем URL из параметров GET
        youtube_url = request.args.get('url', '').strip()
        
        if not youtube_url:
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Укажите параметр url: /download?url=URL_VIDEO"
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=400
            )
        
        # Проверяем валидность URL
        if not is_valid_youtube_url(youtube_url):
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Неверный YouTube URL. Используйте ссылку на YouTube видео."
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=400
            )
        
        # Извлекаем ID видео
        video_id = extract_video_id(youtube_url)
        if not video_id:
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Не удалось извлечь ID видео из URL"
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=400
            )
        
        logger.info(f"Processing video: {video_id}")
        
        # Скачиваем субтитры
        result = download_subtitles(video_id)
        
        if not result:
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Не удалось скачать субтитры. Возможно, их нет или требуется авторизация."
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=404
            )
        
        if not result.get('subtitles'):
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Английские субтитры не найдены для этого видео"
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=404
            )
        
        # Создаем ZIP файл
        zip_filename, clean_title = create_zip_file(
            result['title'], 
            result['subtitles'], 
            video_id
        )
        
        # Формируем ответ
        response_data = {
            'success': True,
            'video_title': result['title'],
            'author': result['author'],
            'video_id': video_id,
            'download_url': f"{request.host_url}download/{zip_filename}",
            'filename': f"{clean_title}.zip",
            'cookies_used': os.path.exists(COOKIES_FILE),
            'language': 'en',
            'subtitle_length': len(result['subtitles'])
        }
        
        logger.info(f"Completed: {result['title']}")
        
        return Response(
            json.dumps(response_data, ensure_ascii=False),
            content_type='application/json; charset=utf-8'
        )
        
    except zipfile.BadZipFile:
        logger.error("Bad zip file created")
        return Response(
            json.dumps({
                'success': False,
                'error': "Ошибка создания архива"
            }, ensure_ascii=False),
            content_type='application/json; charset=utf-8',
            status=500
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return Response(
            json.dumps({
                'success': False,
                'error': "Внутренняя ошибка сервера"
            }, ensure_ascii=False),
            content_type='application/json; charset=utf-8',
            status=500
        )

@app.route('/download/<filename>')
def download_file(filename):
    """Скачивание готового файла с валидацией"""
    try:
        # Валидируем имя файла
        if not validate_filename(filename):
            logger.warning(f"Invalid filename attempt: {filename}")
            abort(404)
        
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        logger.info(f"Sending file: {filename}")
        
        # Используем безопасную отправку файлов
        return send_file(
            filepath,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename,
            conditional=True  # Поддержка If-Modified-Since
        )
        
    except FileNotFoundError:
        logger.warning(f"File not found: {filename}")
        abort(404)
    except Exception as e:
        logger.error(f"Error sending file {filename}: {e}")
        abort(500)

@app.errorhandler(404)
def not_found_error(error):
    return Response(
        json.dumps({
            'success': False,
            'error': 'Ресурс не найден'
        }, ensure_ascii=False),
        content_type='application/json; charset=utf-8',
        status=404
    )

@app.errorhandler(500)
def internal_error(error):
    return Response(
        json.dumps({
            'success': False,
            'error': 'Внутренняя ошибка сервера'
        }, ensure_ascii=False),
        content_type='application/json; charset=utf-8',
        status=500
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    # Дополнительная настройка логирования
    if not app.debug:
        file_handler = logging.FileHandler('error.log')
        file_handler.setLevel(logging.WARNING)
        app.logger.addHandler(file_handler)
    
    logger.info(f"🚀 Запускаю сервер на порту {port}")
    logger.info(f"📁 Папка для файлов: {UPLOAD_FOLDER}")
    logger.info(f"🍪 Cookies файл: {'найден' if os.path.exists(COOKIES_FILE) else 'не найден'}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        threaded=True
    )
