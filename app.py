# app.py - Исправленная версия
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
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
MAX_SUBTITLES_SIZE = 10 * 1024 * 1024
CLEANUP_AGE = 3600

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
    
    allowed_domains = [
        'youtube.com',
        'www.youtube.com',
        'm.youtube.com',
        'youtu.be',
        'www.youtu.be'
    ]
    
    if parsed.netloc not in allowed_domains:
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
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read(1024 * 1024).decode('utf-8')
            video_info = json.loads(data)
            
            return {
                'title': video_info.get('title', 'Unknown Video')[:500],
                'author_name': video_info.get('author_name', 'Unknown Author')[:200],
                'thumbnail_url': video_info.get('thumbnail_url', '')
            }
            
    except Exception as e:
        logger.warning(f"Failed to get video info: {e}")
        return None

def try_alternative_subtitle_methods(video_id):
    """Пробует альтернативные методы получения субтитров"""
    methods = [
        # Метод 1: Прямой запрос к YouTube API
        lambda: get_subtitles_via_api(video_id),
        # Метод 2: Пробуем несколько форматов
        lambda: get_subtitles_multiple_formats(video_id),
        # Метод 3: Пробуем без cookies
        lambda: get_subtitles_no_cookies(video_id)
    ]
    
    for method in methods:
        try:
            result = method()
            if result:
                logger.info(f"Subtitles found using alternative method")
                return result
        except Exception as e:
            logger.debug(f"Alternative method failed: {e}")
            continue
    
    return None

def get_subtitles_via_api(video_id):
    """Пробует получить субтитры через неофициальный API"""
    try:
        # Попытка получить автоматические субтитры
        url = f"https://www.youtube.com/api/timedtext?lang=en&v={video_id}&fmt=srv3"
        
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_content = response.read().decode('utf-8', errors='ignore')
            
            if not xml_content or 'transcript' not in xml_content.lower():
                return None
            
            # Парсим XML субтитры
            text = parse_xml_subtitles(xml_content)
            if text and len(text) > 100:  # Минимальная длина для субтитров
                video_info = get_video_info(video_id) or {
                    'title': 'Unknown Video',
                    'author_name': 'Unknown Author'
                }
                
                return {
                    'title': video_info['title'],
                    'author': video_info['author_name'],
                    'subtitles': text,
                    'video_id': video_id,
                    'source': 'api'
                }
    except Exception as e:
        logger.debug(f"API method failed: {e}")
    
    return None

def get_subtitles_multiple_formats(video_id):
    """Пробует скачать субтитры в нескольких форматах"""
    formats = ['srv3', 'ttml', 'vtt']
    
    for fmt in formats:
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en'],
                'subtitlesformat': fmt,
                'socket_timeout': 15,
                'retries': 1,
                'nooverwrites': True,
                'noplaylist': True,
                'ignoreerrors': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            }
            
            if os.path.exists(COOKIES_FILE):
                try:
                    if os.path.getsize(COOKIES_FILE) <= 1024 * 1024:
                        ydl_opts['cookiefile'] = COOKIES_FILE
                except:
                    pass
            
            with tempfile.TemporaryDirectory(prefix='yt_subtitles_') as temp_dir:
                ydl_opts['outtmpl'] = os.path.join(temp_dir, 'subtitle.%(ext)s')
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    info = ydl.extract_info(url, download=True)
                    
                    # Проверяем наличие субтитров в информации
                    if info and 'subtitles' in info and info['subtitles']:
                        for lang in info['subtitles']:
                            if lang.startswith('en'):
                                for sub in info['subtitles'][lang]:
                                    if sub['ext'] == fmt:
                                        # Загружаем субтитры
                                        if 'url' in sub:
                                            return download_subtitles_from_url(sub['url'], video_id, fmt)
                    
                    # Ищем файл на диске
                    for file in os.listdir(temp_dir):
                        if any(file.endswith(f'.{ext}') for ext in [fmt, 'srt', 'vtt', 'ttml']):
                            filepath = os.path.join(temp_dir, file)
                            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                            
                            text = convert_subtitles_to_text(content, fmt)
                            if text:
                                video_info = get_video_info(video_id) or {
                                    'title': 'Unknown Video',
                                    'author_name': 'Unknown Author'
                                }
                                
                                return {
                                    'title': video_info['title'],
                                    'author': video_info['author_name'],
                                    'subtitles': text,
                                    'video_id': video_id,
                                    'source': f'format_{fmt}'
                                }
                            
        except Exception as e:
            logger.debug(f"Format {fmt} failed: {e}")
            continue
    
    return None

def get_subtitles_no_cookies(video_id):
    """Пробует скачать субтитры без cookies"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'srv3',
            'socket_timeout': 15,
            'retries': 2,
            'nooverwrites': True,
            'noplaylist': True,
            'ignoreerrors': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate'
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['dash'],
                    'player_skip': ['configs', 'webpage']
                }
            }
        }
        
        with tempfile.TemporaryDirectory(prefix='yt_subtitles_') as temp_dir:
            ydl_opts['outtmpl'] = os.path.join(temp_dir, 'subtitle.%(ext)s')
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={video_id}"
                ydl.download([url])
            
            # Ищем любой файл субтитров
            for file in os.listdir(temp_dir):
                if any(file.endswith(ext) for ext in ['.srv3', '.srt', '.vtt', '.ttml', '.xml']):
                    filepath = os.path.join(temp_dir, file)
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    ext = os.path.splitext(file)[1][1:]
                    text = convert_subtitles_to_text(content, ext)
                    
                    if text and len(text) > 100:
                        video_info = get_video_info(video_id) or {
                            'title': 'Unknown Video',
                            'author_name': 'Unknown Author'
                        }
                        
                        return {
                            'title': video_info['title'],
                            'author': video_info['author_name'],
                            'subtitles': text,
                            'video_id': video_id,
                            'source': 'no_cookies'
                        }
    
    except Exception as e:
        logger.debug(f"No cookies method failed: {e}")
    
    return None

def download_subtitles_from_url(subtitle_url, video_id, fmt):
    """Скачивает субтитры по прямой ссылке"""
    try:
        req = urllib.request.Request(subtitle_url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8', errors='ignore')
            
            text = convert_subtitles_to_text(content, fmt)
            if text:
                video_info = get_video_info(video_id) or {
                    'title': 'Unknown Video',
                    'author_name': 'Unknown Author'
                }
                
                return {
                    'title': video_info['title'],
                    'author': video_info['author_name'],
                    'subtitles': text,
                    'video_id': video_id,
                    'source': 'direct_url'
                }
    except Exception as e:
        logger.debug(f"Failed to download from URL: {e}")
    
    return None

def parse_xml_subtitles(xml_content):
    """Парсит XML субтитры YouTube"""
    try:
        # Упрощенный парсинг XML субтитров
        text_parts = []
        
        # Удаляем XML теги и извлекаем текст
        xml_content = re.sub(r'<\?xml.*?\?>', '', xml_content, flags=re.DOTALL)
        xml_content = re.sub(r'<text[^>]*>', '', xml_content)
        xml_content = re.sub(r'</text>', ' ', xml_content)
        
        # Удаляем остальные теги
        xml_content = re.sub(r'<[^>]+>', '', xml_content)
        
        # Убираем лишние пробелы
        text = re.sub(r'\s+', ' ', xml_content).strip()
        
        return text
    except Exception as e:
        logger.debug(f"Failed to parse XML subtitles: {e}")
        return ""

def convert_subtitles_to_text(content, fmt):
    """Конвертирует субтитры в текстовый формат"""
    if not content:
        return ""
    
    try:
        if fmt in ['srv3', 'xml', 'ttml']:
            return parse_xml_subtitles(content)
        elif fmt == 'vtt':
            return vtt_to_text(content)
        elif fmt == 'srt':
            return srt_to_text(content)
        else:
            # Пробуем автоматически определить формат
            if 'WEBVTT' in content[:100]:
                return vtt_to_text(content)
            elif re.search(r'\d+\s+\d{2}:\d{2}:\d{2}', content[:500]):
                return srt_to_text(content)
            elif '<transcript>' in content.lower() or '<text ' in content:
                return parse_xml_subtitles(content)
            else:
                # Простой метод - убираем временные метки
                lines = content.split('\n')
                text_lines = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Пропускаем строки с временными метками
                    if '-->' in line or re.match(r'^\d{2}:\d{2}', line):
                        continue
                    # Пропускаем номера строк
                    if line.isdigit() and len(text_lines) > 0:
                        continue
                    text_lines.append(line)
                
                return ' '.join(text_lines).strip()
    except Exception as e:
        logger.debug(f"Failed to convert {fmt} subtitles: {e}")
        return ""

def srt_to_text(srt_content):
    """Конвертирует SRT формат в чистый текст"""
    if not srt_content:
        return ""
    
    lines = srt_content.split('\n')
    text_lines = []
    
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or '-->' in line:
            continue
        line = re.sub(r'<[^>]+>', '', line)
        text_lines.append(line)
    
    text = ' '.join(text_lines)
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()[:MAX_SUBTITLES_SIZE]

def vtt_to_text(vtt_content):
    """Конвертирует VTT формат в чистый текст"""
    if not vtt_content:
        return ""
    
    lines = vtt_content.split('\n')
    text_lines = []
    in_cue = False
    
    for line in lines:
        line = line.strip()
        
        if not line:
            continue
        
        # Пропускаем заголовок WEBVTT
        if line.startswith('WEBVTT'):
            continue
        
        # Пропускаем заметки и регионы
        if line.startswith('NOTE ') or line.startswith('REGION '):
            continue
        
        # Пропускаем временные метки
        if '-->' in line:
            in_cue = True
            continue
        
        # Пропускаем стили
        if line.startswith('STYLE '):
            continue
        
        if in_cue and line:
            line = re.sub(r'<[^>]+>', '', line)
            text_lines.append(line)
    
    text = ' '.join(text_lines)
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()[:MAX_SUBTITLES_SIZE]

def download_subtitles(video_id):
    """Основная функция скачивания субтитров с fallback методами"""
    if not is_valid_video_id(video_id):
        return None
    
    logger.info(f"Attempting to download subtitles for video: {video_id}")
    
    # Метод 1: Стандартный метод с yt-dlp
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'srv3',  # Используем srv3 как основной формат
            'socket_timeout': 20,
            'retries': 2,
            'nooverwrites': True,
            'noplaylist': True,
            'ignoreerrors': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'en-US,en;q=0.9'
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['dash']
                }
            }
        }
        
        # Добавляем cookies если файл существует и валиден
        if os.path.exists(COOKIES_FILE):
            try:
                if os.path.getsize(COOKIES_FILE) <= 1024 * 1024:
                    ydl_opts['cookiefile'] = COOKIES_FILE
                    logger.info("Using cookies.txt")
            except OSError as e:
                logger.warning(f"Cannot read cookies file: {e}")
        
        with tempfile.TemporaryDirectory(prefix='yt_subtitles_') as temp_dir:
            ydl_opts['outtmpl'] = os.path.join(temp_dir, 'subtitle.%(ext)s')
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={video_id}"
                
                # Получаем информацию без скачивания сначала
                info = ydl.extract_info(url, download=False)
                
                if info and 'subtitles' in info:
                    logger.info(f"Available subtitles: {list(info.get('subtitles', {}).keys())}")
                    logger.info(f"Automatic subtitles: {list(info.get('automatic_captions', {}).keys())}")
                
                # Теперь пробуем скачать
                result = ydl.download([url])
                
                if result == 0:  # Успешное завершение
                    # Ищем файл субтитров
                    for file in os.listdir(temp_dir):
                        if any(file.endswith(ext) for ext in ['.srv3', '.srt', '.vtt', '.ttml', '.xml']):
                            filepath = os.path.join(temp_dir, file)
                            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                            
                            if content:
                                ext = os.path.splitext(file)[1][1:]
                                text = convert_subtitles_to_text(content, ext)
                                
                                if text and len(text) > 100:
                                    video_info = get_video_info(video_id) or {
                                        'title': 'Unknown Video',
                                        'author_name': 'Unknown Author'
                                    }
                                    
                                    logger.info(f"Successfully downloaded subtitles via standard method")
                                    
                                    return {
                                        'title': video_info['title'],
                                        'author': video_info['author_name'],
                                        'subtitles': text,
                                        'video_id': video_id,
                                        'source': 'standard'
                                    }
    
    except Exception as e:
        logger.warning(f"Standard download method failed: {e}")
    
    # Метод 2: Пробуем альтернативные методы
    logger.info("Trying alternative methods...")
    alternative_result = try_alternative_subtitle_methods(video_id)
    
    if alternative_result:
        return alternative_result
    
    # Метод 3: Пробуем ручной API запрос как последнее средство
    logger.info("Trying manual API request...")
    manual_result = get_subtitles_via_api(video_id)
    
    if manual_result:
        return manual_result
    
    logger.error(f"All methods failed for video: {video_id}")
    return None

def create_zip_file(video_title, subtitles_text, video_id):
    """Создает ZIP файл с субтитрами"""
    clean_title = re.sub(r'[<>:"/\\|?*]', '_', video_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    if len(clean_title) > 100:
        clean_title = clean_title[:100]
    
    if not clean_title:
        clean_title = "subtitles"
    
    zip_filename = f"{video_id}_{uuid.uuid4().hex[:8]}.zip"
    zip_filepath = os.path.join(UPLOAD_FOLDER, zip_filename)
    
    safe_internal_name = secure_filename(f"{clean_title}.txt")
    if not safe_internal_name.endswith('.txt'):
        safe_internal_name += '.txt'
    
    try:
        with zipfile.ZipFile(zip_filepath, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            content = f"{video_title}\n\n{subtitles_text}"
            zipf.writestr(safe_internal_name, content.encode('utf-8'))
        
        logger.info(f"Created zip file: {zip_filename}")
        return zip_filename, clean_title
        
    except Exception as e:
        logger.error(f"Error creating zip file: {e}")
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
    
    if filename != secure_filename(filename):
        return False
    
    if not re.match(r'^[a-zA-Z0-9_-]{11}_[a-f0-9]{8}\.zip$', filename):
        return False
    
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(filepath):
        return False
    
    return True

@app.route('/')
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
            .error {{ background: #f8d7da; color: #721c24; }}
            .form {{ margin: 20px 0; }}
            .input {{ width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }}
            .button {{ background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }}
            .button:hover {{ background: #0056b3; }}
            .loading {{ display: none; color: #666; }}
        </style>
    </head>
    <body>
        <h1>🚀 YouTube Subtitles Downloader</h1>
        
        <div class="status {'success' if os.path.exists(COOKIES_FILE) else 'warning'}">
            <strong>Статус:</strong> Cookies: {cookies_status} | Файлов в кэше: {files_count}
        </div>
        
        <h2>📥 Скачать субтитры:</h2>
        
        <div class="form">
            <input type="text" id="url" class="input" placeholder="https://youtube.com/watch?v=VIDEO_ID" value="https://youtube.com/watch?v=dQw4w9WgXcQ">
            <button onclick="downloadSubtitles()" class="button">Скачать субтитры</button>
            <div id="loading" class="loading">⏳ Обработка, может занять до 30 секунд...</div>
        </div>
        
        <div id="result" style="margin: 20px 0;"></div>
        
        <h2>📋 Примеры:</h2>
        <p>Попробуйте эти видео для теста:</p>
        <ul>
            <li><a href="#" onclick="setUrl('https://youtube.com/watch?v=dQw4w9WgXcQ')">Rick Astley - Never Gonna Give You Up</a></li>
            <li><a href="#" onclick="setUrl('https://youtube.com/watch?v=9bZkp7q19f0')">PSY - GANGNAM STYLE</a></li>
            <li><a href="#" onclick="setUrl('https://youtube.com/watch?v=jNQXAC9IVRw')">Me at the zoo</a></li>
        </ul>
        
        <h3>API Endpoint:</h3>
        <pre>
        GET /download?url=URL_VIDEO
        </pre>
        
        <script>
            function setUrl(url) {{
                document.getElementById('url').value = url;
                return false;
            }}
            
            function downloadSubtitles() {{
                const url = document.getElementById('url').value.trim();
                const resultDiv = document.getElementById('result');
                const loadingDiv = document.getElementById('loading');
                
                if (!url) {{
                    resultDiv.innerHTML = '<div class="status warning">Введите URL видео</div>';
                    return;
                }}
                
                resultDiv.innerHTML = '';
                loadingDiv.style.display = 'block';
                
                fetch(`/download?url=${{encodeURIComponent(url)}}`)
                    .then(response => {{
                        if (!response.ok) {{
                            throw new Error(`HTTP error! Status: ${{response.status}}`);
                        }}
                        return response.json();
                    }})
                    .then(data => {{
                        loadingDiv.style.display = 'none';
                        
                        if (data.success) {{
                            resultDiv.innerHTML = `
                                <div class="status success">
                                    <strong>✅ Субтитры скачаны!</strong><br>
                                    <strong>Видео:</strong> ${{data.video_title}}<br>
                                    <strong>Автор:</strong> ${{data.author}}<br>
                                    <strong>ID видео:</strong> ${{data.video_id}}<br>
                                    <strong>Символов:</strong> ${{data.subtitle_length}}<br>
                                    <strong>Метод:</strong> ${{data.source || 'standard'}}<br>
                                    <strong>Cookies:</strong> ${{data.cookies_used ? 'использовались' : 'не использовались'}}<br><br>
                                    <a href="${{data.download_url}}" class="button" target="_blank">📥 Скачать ZIP файл</a>
                                </div>
                            `;
                        }} else {{
                            resultDiv.innerHTML = `<div class="status error">❌ Ошибка: ${{data.error}}</div>`;
                        }}
                    }})
                    .catch(error => {{
                        loadingDiv.style.display = 'none';
                        resultDiv.innerHTML = `<div class="status error">❌ Ошибка сети: ${{error.message}}</div>`;
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
        
        if not is_valid_youtube_url(youtube_url):
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Неверный YouTube URL"
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=400
            )
        
        video_id = extract_video_id(youtube_url)
        if not video_id:
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Не удалось извлечь ID видео"
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
                    'error': "Субтитры не найдены для этого видео. Возможно, их нет или видео приватное."
                }, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                status=404
            )
        
        if not result.get('subtitles') or len(result['subtitles'].strip()) < 50:
            return Response(
                json.dumps({
                    'success': False,
                    'error': "Субтитры найдены, но слишком короткие или пустые"
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
        
        response_data = {
            'success': True,
            'video_title': result['title'],
            'author': result['author'],
            'video_id': video_id,
            'download_url': f"{request.host_url}download/{zip_filename}",
            'filename': f"{clean_title}.zip",
            'cookies_used': os.path.exists(COOKIES_FILE),
            'language': 'en',
            'subtitle_length': len(result['subtitles']),
            'source': result.get('source', 'standard')
        }
        
        logger.info(f"Completed: {result['title']}")
        
        return Response(
            json.dumps(response_data, ensure_ascii=False),
            content_type='application/json; charset=utf-8'
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
    """Скачивание готового файла"""
    try:
        if not validate_filename(filename):
            logger.warning(f"Invalid filename attempt: {filename}")
            abort(404)
        
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        logger.info(f"Sending file: {filename}")
        
        return send_file(
            filepath,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename,
            conditional=True
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
    
    logger.info(f"🚀 Запускаю сервер на порту {port}")
    logger.info(f"📁 Папка для файлов: {UPLOAD_FOLDER}")
    logger.info(f"🍪 Cookies файл: {'найден' if os.path.exists(COOKIES_FILE) else 'не найден'}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        threaded=True
    )
