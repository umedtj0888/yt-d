from flask import Flask, request, Response
import json
import os
import time
import re
import tempfile
import uuid
import zipfile
import yt_dlp
import urllib.request
import random
#import socks
import socket
from urllib.parse import quote

app = Flask(__name__)
UPLOAD_FOLDER = 'subtitles'
COOKIES_FILE = 'cookies.txt'
LOCAL_TOKEN_FILE = 'oauth_token.txt' # Локальный файл для токена

# Создаем папки если их нет
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def cleanup_old_files():
    """Удаляет старые файлы (старше 1 часа)"""
    try:
        now = time.time()
        for filename in os.listdir(UPLOAD_FOLDER):
            if filename.endswith('.zip'):
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                if os.path.getmtime(filepath) < now - 3600:
                    os.remove(filepath)
    except Exception:
        pass

def extract_video_id(youtube_url):
    """Извлекает ID видео из URL"""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11})',
        r'youtube\.com\/embed\/([^\/\?]+)',
        r'youtu\.be\/([^\?]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    return None

def get_video_info(video_id):
    """Быстро получает информацию о видео"""
    try:
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            return {
                'title': data.get('title', 'Unknown Video'),
                'author_name': data.get('author_name', 'Unknown Author')
            }
    except Exception:
        return {'title': 'Unknown Video', 'author_name': 'Unknown Author'}

def detect_language_from_title(title):
    """Простая детекция языка по заголовку видео"""
    if not title:
        return None
    
    title_lower = title.lower()
    
    # Русский язык
    russian_chars = 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
    if any(char in title_lower for char in russian_chars):
        return 'ru'
    
    # Немецкий
    german_chars = 'äöüß'
    if any(char in title_lower for char in german_chars):
        return 'de'
    
    # Французский (простые проверки)
    french_words = ['les', 'des', 'est', 'pour', 'dans', 'une', 'un']
    if any(word in title_lower.split() for word in french_words):
        return 'fr'
    
    # Испанский
    spanish_words = ['el', 'la', 'los', 'las', 'y', 'que', 'del']
    if any(word in title_lower.split() for word in spanish_words):
        return 'es'
    
    # Португальский
    portuguese_words = ['o', 'a', 'os', 'as', 'do', 'da', 'em']
    if any(word in title_lower.split() for word in portuguese_words):
        return 'pt'
    
    # Итальянский
    italian_words = ['il', 'la', 'i', 'le', 'del', 'nel']
    if any(word in title_lower.split() for word in italian_words):
        return 'it'
    
    # По умолчанию английский (но может быть и другой)
    return 'en'

def clean_any_content(content, ext):
    """Универсальный очиститель контента"""
    if not content:
        return None
        
    # 1. Проверка на JSON3 (новый формат YouTube)
    if ext == 'json3' or content.strip().startswith('{'):
        try:
            data = json.loads(content)
            text_parts = []
            events = data.get('events', [])
            for event in events:
                for seg in event.get('segs', []):
                    line = seg.get('utf8', '').strip()
                    if line and not (line.startswith('[') and line.endswith(']')):
                        text_parts.append(line)
            result = ' '.join(text_parts).strip()
            return result if len(result) > 50 else None
        except:
            pass # Не JSON, идем дальше
            
    # 2. Очистка SRT/VTT/SRV3
    lines = content.split('\n')
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or '-->' in line:
            continue
        line = re.sub(r'<[^>]+>', '', line)
        text_lines.append(line)
    
    result = ' '.join(text_lines).strip()
    return result if len(result) > 50 else None

def get_subtitles_with_priority(info, video_title):
    """Извлекает субтитры согласно приоритету языков"""
    if not info:
        return None, None, None
    
    # Определяем язык видео
    video_lang = detect_language_from_title(video_title)
    print(f"🎯 Определен язык видео: {video_lang}")
    
    # Приоритеты согласно требованиям:
    # 1. Английские ручные, если нет - английские авто
    # 2. Русские ручные, если нет - русские авто
    # 3. Язык видео (ручные, если нет - авто)
    # 4. Любой доступный язык
    
    # Шаг 1: Проверяем английский
    print("\n🔍 ШАГ 1: Ищем английские субтитры...")
    result = check_language_with_priority(info, 'en', video_title)
    if result:
        return result
    
    # Шаг 2: Проверяем русский
    print("\n🔍 ШАГ 2: Ищем русские субтитры...")
    result = check_language_with_priority(info, 'ru', video_title)
    if result:
        return result
    
    # Шаг 3: Проверяем язык видео (если это не en или ru)
    print("\n🔍 ШАГ 3: Ищем субтитры на языке видео...")
    if video_lang and video_lang not in ['en', 'ru']:
        result = check_language_with_priority(info, video_lang, video_title)
        if result:
            return result
    
    # Шаг 4: Ищем любой доступный язык
    print("\n🔍 ШАГ 4: Ищем любой доступный язык...")
    result = check_any_available_language(info, video_title)
    if result:
        return result
    
    print("❌ Не удалось найти субтитры ни на одном языке")
    return None, None, None

def check_language_with_priority(info, language, video_title):
    """Проверяет субтитры для конкретного языка (сначала ручные, потом авто)"""
    print(f"   🔎 Проверяем язык: {language}")
    
    # Сначала проверяем ручные субтитры
    manual_subs = info.get('subtitles')
    if manual_subs and language in manual_subs:
        print(f"   ✅ Найдены ручные субтитры на {language}")
        text, format_type = download_and_process_subs(manual_subs[language], language, 'manual')
        if text:
            return text, language, 'manual'
    
    # Если нет ручных, проверяем автоматические
    auto_subs = info.get('automatic_captions')
    if auto_subs and language in auto_subs:
        print(f"   ✅ Найдены автоматические субтитры на {language}")
        text, format_type = download_and_process_subs(auto_subs[language], language, 'auto')
        if text:
            return text, language, 'auto'
    
    print(f"   ❌ Субтитры на языке {language} не найдены")
    return None

def check_any_available_language(info, video_title):
    """Ищет любой доступный язык (сначала ручные, потом авто)"""
    # Сначала проверяем ручные субтитры
    manual_subs = info.get('subtitles')
    if manual_subs:
        available_langs = list(manual_subs.keys())
        if available_langs:
            # Ищем популярные языки в первую очередь
            popular_langs = ['en', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh']
            for lang in popular_langs:
                if lang in available_langs:
                    print(f"   ✅ Найдены ручные субтитры на {lang}")
                    text, format_type = download_and_process_subs(manual_subs[lang], lang, 'manual')
                    if text:
                        return text, lang, 'manual'
            
            # Берем первый доступный язык
            first_lang = available_langs[0]
            print(f"   ✅ Найдены ручные субтитры на {first_lang}")
            text, format_type = download_and_process_subs(manual_subs[first_lang], first_lang, 'manual')
            if text:
                return text, first_lang, 'manual'
    
    # Если нет ручных, проверяем автоматические
    auto_subs = info.get('automatic_captions')
    if auto_subs:
        available_langs = list(auto_subs.keys())
        if available_langs:
            # Ищем популярные языки
            popular_langs = ['en', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh']
            for lang in popular_langs:
                if lang in available_langs:
                    print(f"   ✅ Найдены автоматические субтитры на {lang}")
                    text, format_type = download_and_process_subs(auto_subs[lang], lang, 'auto')
                    if text:
                        return text, lang, 'auto'
            
            # Берем первый доступный язык
            first_lang = available_langs[0]
            print(f"   ✅ Найдены автоматические субтитры на {first_lang}")
            text, format_type = download_and_process_subs(auto_subs[first_lang], first_lang, 'auto')
            if text:
                return text, first_lang, 'auto'
    
    return None

def download_and_process_subs(subs_list, language, source_type):
    """Скачивает и обрабатывает субтитры из списка"""
    # Ищем лучший формат
    preferred_formats = ['json3', 'json', 'srv3', 'vtt', 'srt']
    best_url = None
    best_format = None
    best_score = 100
    
    for sub in subs_list:
        if 'url' not in sub:
            continue
        
        ext = sub.get('ext', '')
        score = preferred_formats.index(ext) if ext in preferred_formats else 99
        if score < best_score:
            best_score = score
            best_url = sub['url']
            best_format = ext
    
    if best_url:
        print(f"   📥 Скачиваем {best_format} формат...")
        try:
            req = urllib.request.Request(best_url, headers={
                'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 11) gzip',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0'
            })
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read().decode('utf-8', errors='ignore')
            
            text = clean_any_content(raw, best_format)
            if text:
                print(f"   ✅ Успешно получены {source_type} субтитры")
                return text, best_format
            else:
                print(f"   ⚠️ Текст слишком короткий или пустой")
        except Exception as e:
            print(f"   ⚠️ Ошибка скачивания: {e}")
    
    return None, None

def get_random_user_agent():
    """Генерирует случайный User-Agent"""
    user_agents = [
        # Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        
        # Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        
        # Linux
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        
        # Android
        'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36',
        'com.google.android.youtube/17.36.4 (Linux; U; Android 11) gzip',
        
        # iOS
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
    ]
    return random.choice(user_agents)

def download_subtitles(video_id):
    """Скачивает субтитры с использованием улучшенной маскировки"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Случайные параметры для маскировки
    user_agent = get_random_user_agent()
    referer = f"https://www.youtube.com/watch?v={video_id}"
    
    ydl_opts = {
        'quiet': False,
        'verbose': False,
        'no_warnings': True,
        'skip_download': True,
        'ignoreerrors': True,
        'no_check_certificate': True,
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],  # Пробуем разные клиенты
                'player_skip': ['webpage', 'configs'],
                'lang': ['en', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh']
            }
        },
        'socket_timeout': 30,
        'http_headers': {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8,es;q=0.7,fr;q=0.6,de;q=0.5,it;q=0.4,pt;q=0.3,ja;q=0.2,ko;q=0.1,zh;q=0.1',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Referer': referer,
            'Origin': 'https://www.youtube.com'
        }
    }
    
    # Попробуем несколько методов авторизации по порядку
    auth_methods_tried = []
    
    # Метод 1: OAuth токен (приоритетный)
    oauth_token = os.environ.get('OAUTH_TOKEN')
    if not oauth_token and os.path.exists(LOCAL_TOKEN_FILE):
        with open(LOCAL_TOKEN_FILE, 'r') as f:
            oauth_token = f.read().strip()
    
    if oauth_token:
        ydl_opts['oauth_refresh_token'] = oauth_token
        auth_methods_tried.append('OAuth')
        print("🔑 Метод 1: Используем OAuth Токен")
    
    # Метод 2: Cookies файл
    elif os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        auth_methods_tried.append('Cookies')
        print("🍪 Метод 2: Используем cookies.txt")
    
    # Метод 3: Без авторизации (последний вариант)
    else:
        auth_methods_tried.append('None')
        print("⚠️ Метод 3: Без авторизации - используем публичный доступ")
        # Добавляем дополнительные параметры для публичного доступа
        ydl_opts['extractor_args']['youtube']['player_client'].append('ios')
        ydl_opts['extractor_args']['youtube']['player_client'].append('tv')
    
    try:
        print(f"🌐 Используем User-Agent: {user_agent[:50]}...")
        print(f"🔗 Referer: {referer}")
        
        # Попробуем несколько раз с разными настройками
        max_retries = 2
        for attempt in range(max_retries):
            try:
                print(f"\n🔄 Попытка {attempt + 1}/{max_retries}")
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Получаем информацию о видео
                    info = ydl.extract_info(url, download=False)
                    
                    if not info:
                        print("❌ Не удалось получить информацию о видео")
                        if attempt < max_retries - 1:
                            print("🔄 Пробуем еще раз...")
                            time.sleep(1)
                            continue
                        return None
                    
                    # Получаем информацию о видео для определения языка
                    video_info = get_video_info(video_id)
                    video_title = video_info['title']
                    
                    # Получаем субтитры согласно приоритету
                    text, detected_lang, source_type = get_subtitles_with_priority(info, video_title)
                    
                    if text:
                        print(f"✅ Успешно на попытке {attempt + 1}")
                        return {
                            'title': video_title,
                            'author': video_info['author_name'],
                            'subtitles': text,
                            'video_id': video_id,
                            'language': detected_lang,
                            'source_type': source_type,
                            'auth_method': auth_methods_tried[0] if auth_methods_tried else 'None'
                        }
                    else:
                        print(f"⚠️ Субтитры не найдены на попытке {attempt + 1}")
                        if attempt < max_retries - 1:
                            # Меняем User-Agent для следующей попытки
                            ydl_opts['http_headers']['User-Agent'] = get_random_user_agent()
                            print(f"🔄 Меняем User-Agent и пробуем еще раз...")
                            time.sleep(2)
                            continue
            
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                print(f"❌ Ошибка yt-dlp: {error_msg[:100]}")
                
                # Если это ошибка "Sign in to confirm you're not a bot"
                if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                    print("🚫 Обнаружена блокировка бота")
                    if attempt < max_retries - 1:
                        # Пробуем другой подход
                        print("🔄 Пробуем альтернативный метод...")
                        
                        # Пробуем скачать через embed страницу
                        embed_url = f"https://www.youtube.com/embed/{video_id}"
                        ydl_opts['http_headers']['Referer'] = embed_url
                        
                        # Меняем User-Agent на мобильный
                        ydl_opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (Linux; Android 10; SM-G960F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
                        
                        time.sleep(3)
                        continue
                
                elif attempt < max_retries - 1:
                    print(f"🔄 Пробуем еще раз ({attempt + 2}/{max_retries})...")
                    time.sleep(1)
                    continue
                else:
                    print("❌ Все попытки не удались")
                    return None
            
            except Exception as e:
                print(f"❌ Общая ошибка: {e}")
                if attempt < max_retries - 1:
                    print(f"🔄 Пробуем еще раз...")
                    time.sleep(1)
                    continue
        
        print("❌ Не удалось найти текст субтитров после всех попыток")
        return None
        
    except Exception as e:
        print(f"❌ Критическая ошибка скачивания: {e}")
        return None

def create_zip_file(video_title, subtitles_text, video_id, language, source_type):
    """Создает ZIP файл с субтитрами"""
    clean_title = re.sub(r'[<>:"/\\|?*]', '_', video_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    if len(clean_title) > 50:
        clean_title = clean_title[:50]
    
    # Добавляем информацию о языке и типе в имя файла
    lang_display = language.upper() if language else 'UNK'
    type_display = 'MAN' if source_type == 'manual' else 'AUTO'
    zip_filename = f"{video_id}_{lang_display}_{type_display}_{uuid.uuid4().hex[:4]}.zip"
    zip_filepath = os.path.join(UPLOAD_FOLDER, zip_filename)
    
    # Добавляем метаданные в файл
    source_type_display = "Ручные субтитры" if source_type == 'manual' else "Автоматические субтитры"
    metadata = f"""Видео: {video_title}
Язык: {language}
Тип субтитров: {source_type_display}
Видео ID: {video_id}
Дата создания: {time.strftime('%Y-%m-%d %H:%M:%S')}

"""
    
    content = metadata + subtitles_text
    
    with zipfile.ZipFile(zip_filepath, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
        zipf.writestr(f"{clean_title}.txt", content.encode('utf-8'))
    
    return zip_filename, clean_title

def error_response(message):
    """Возвращает ошибку в JSON формате"""
    return Response(
        json.dumps({
            'success': False,
            'error': message
        }, ensure_ascii=False),
        content_type='application/json; charset=utf-8',
        status=400
    )

@app.route('/')
def home():
    """Главная страница с инструкцией"""
    cleanup_old_files()
    
    cookies_status = "✅ Найден" if os.path.exists(COOKIES_FILE) else "❌ Не найден"
    token_status = "✅ Найден" if os.path.exists(LOCAL_TOKEN_FILE) else "❌ Не найден"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>🚀 YouTube Subtitles Downloader</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
            pre {{ background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }}
            .status {{ padding: 10px; margin: 10px 0; border-radius: 5px; }}
            .success {{ background: #d4edda; color: #155724; }}
            .warning {{ background: #fff3cd; color: #856404; }}
            .info {{ background: #d1ecf1; color: #0c5460; }}
            .priority {{ margin: 15px 0; padding-left: 20px; }}
            .priority li {{ margin: 5px 0; }}
            .test-btn {{ display: inline-block; padding: 10px 20px; background: #28a745; color: white; text-decoration: none; border-radius: 5px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <h1>🚀 YouTube Subtitles Downloader</h1>
        
        <div class="info status">
            <strong>🎯 Приоритет языков:</strong>
            <ol class="priority">
                <li><strong>Английский</strong> (ручные → автоматические)</li>
                <li><strong>Русский</strong> (ручные → автоматические)</li>
                <li><strong>Язык видео</strong> (определяется по заголовку)</li>
                <li><strong>Любой доступный язык</strong></li>
            </ol>
        </div>
        
        <div class="status {'success' if os.path.exists(COOKIES_FILE) else 'warning'}">
            <strong>🍪 Cookies.txt:</strong> {cookies_status}
        </div>
        
        <div class="status {'success' if os.path.exists(LOCAL_TOKEN_FILE) else 'warning'}">
            <strong>🔑 OAuth Token:</strong> {token_status}
        </div>
        
        <div class="info status">
            <strong>🛡️ Анти-блок система:</strong> Используется рандомизация User-Agent, ретраи и альтернативные методы доступа
        </div>
        
        <h2>📋 Использование API</h2>
        <p>Два способа использования:</p>
        
        <h3>1. GET запрос (простой):</h3>
        <pre>
GET /download?url=https://youtube.com/watch?v=VIDEO_ID
        </pre>
        <p>Пример в браузере:</p>
        <pre>
<a href="/download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" target="_blank">
    /download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ
</a>
        </pre>
        
        <a href="/test" class="test-btn">🎬 Тестовая страница</a>
        
        <h3>2. POST запрос (рекомендуется для приложений):</h3>
        <pre>
POST /download
Content-Type: application/json

{{"url": "https://youtube.com/watch?v=VIDEO_ID"}}
        </pre>
        
        <h2>📝 Пример через curl:</h2>
        <pre>
# GET запрос
curl "https://ваш-сервис.onrender.com/download?url=https://youtube.com/watch?v=dQw4w9WgXcQ"

# POST запрос
curl -X POST https://ваш-сервис.onrender.com/download \\
  -H "Content-Type: application/json" \\
  -d '{{"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}}'
        </pre>
        
        <h2>📊 Пример ответа:</h2>
        <pre>
{{
    "success": true,
    "video_title": "Название видео",
    "author": "Автор",
    "video_id": "VIDEO_ID",
    "download_url": "https://.../download/filename.zip",
    "language": "en",
    "language_display": "English",
    "source_type": "manual",
    "source_type_display": "Ручные субтитры"
}}
        </pre>
        
        <h2>🌍 Поддерживаемые языки:</h2>
        <ul>
            <li><strong>English (en)</strong> - 1-й приоритет</li>
            <li><strong>Russian (ru)</strong> - 2-й приоритет</li>
            <li>Spanish (es), French (fr), German (de)</li>
            <li>Italian (it), Portuguese (pt)</li>
            <li>Japanese (ja), Korean (ko), Chinese (zh)</li>
            <li>И другие доступные языки</li>
        </ul>
        
        <h2>⚠️ Решение проблем:</h2>
        <p>Если возникают ошибки "bot detection":</p>
        <ol>
            <li>Обновите cookies.txt файл на актуальный</li>
            <li>Используйте OAuth токен (более надежно)</li>
            <li>Система автоматически пробует альтернативные методы</li>
        </ol>
    </body>
    </html>
    '''

@app.route('/download', methods=['GET', 'POST'])
def download_subtitles_route():
    """Основной эндпоинт для скачивания субтитров (поддерживает GET и POST)"""
    cleanup_old_files()
    
    try:
        youtube_url = None
        
        # Обработка GET запроса с параметром ?url=
        if request.method == 'GET':
            youtube_url = request.args.get('url')
            if not youtube_url:
                # Если GET запрос без параметра, показываем инструкцию
                return '''
                <!DOCTYPE html>
                <html>
                <head><title>YouTube Subtitles Downloader - GET</title></head>
                <body>
                    <h1>📥 GET Download</h1>
                    <p>Используйте параметр ?url= для скачивания субтитров:</p>
                    <pre>
    /download?url=https://youtube.com/watch?v=VIDEO_ID
                    </pre>
                    <p>Пример:</p>
                    <pre>
    <a href="/download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ">
        /download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ
    </a>
                    </pre>
                    <p><a href="/">← Назад на главную</a></p>
                </body>
                </html>
                '''
        
        # Обработка POST запроса с JSON
        elif request.method == 'POST':
            data = request.get_json()
            if not data or 'url' not in data:
                return error_response("Отправьте JSON с URL: {\"url\": \"...\"}")
            youtube_url = data['url'].strip()
        
        if not youtube_url:
            return error_response("Введите URL видео")
        
        video_id = extract_video_id(youtube_url)
        if not video_id:
            return error_response("Неверный YouTube URL")
        
        print(f"\n" + "="*60)
        print(f"📥 Обработка видео: {video_id}")
        print(f"🔗 URL: {youtube_url}")
        print(f"📡 Метод: {request.method}")
        print(f"🌐 Хост: {request.host}")
        print("="*60)
        
        result = download_subtitles(video_id)
        
        if not result:
            return error_response("Не удалось скачать субтитры (YouTube заблокировал запрос)")
        
        if not result.get('subtitles'):
            return error_response("Субтитры не найдены для этого видео")
        
        language = result.get('language', 'unknown')
        source_type = result.get('source_type', 'unknown')
        zip_filename, clean_title = create_zip_file(
            result['title'], 
            result['subtitles'], 
            video_id,
            language,
            source_type
        )
        
        # Определяем отображаемое название языка
        language_names = {
            'en': 'English',
            'ru': 'Russian',
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'it': 'Italian',
            'pt': 'Portuguese',
            'ja': 'Japanese',
            'ko': 'Korean',
            'zh': 'Chinese',
            'ar': 'Arabic',
            'hi': 'Hindi',
            'uk': 'Ukrainian',
            'pl': 'Polish',
            'tr': 'Turkish',
            'nl': 'Dutch',
            'sv': 'Swedish',
            'da': 'Danish',
            'no': 'Norwegian',
            'fi': 'Finnish',
            'cs': 'Czech',
            'sk': 'Slovak',
            'hu': 'Hungarian',
            'ro': 'Romanian',
            'bg': 'Bulgarian',
            'el': 'Greek',
            'he': 'Hebrew',
            'th': 'Thai',
            'vi': 'Vietnamese',
            'id': 'Indonesian',
            'ms': 'Malay',
            'fil': 'Filipino'
        }
        
        language_display = language_names.get(language, language)
        source_type_display = "Ручные субтитры" if source_type == 'manual' else "Автоматические субтитры"
        
        response_data = {
            'success': True,
            'video_title': result['title'],
            'author': result['author'],
            'video_id': video_id,
            'download_url': f"{request.host_url}download/{zip_filename}",
            'filename': f"{clean_title}.zip",
            'language': language,
            'language_display': language_display,
            'source_type': source_type,
            'source_type_display': source_type_display,
            'priority_used': get_priority_used(language, source_type),
            'auth_method': result.get('auth_method', 'unknown')
        }
        
        print(f"\n✅ Готово!")
        print(f"📺 Видео: {result['title']}")
        print(f"👤 Автор: {result['author']}")
        print(f"🌐 Язык: {language_display} ({language})")
        print(f"📝 Тип: {source_type_display}")
        print(f"🎯 Использован приоритет: {response_data['priority_used']}")
        print(f"🔐 Метод авторизации: {response_data['auth_method']}")
        print(f"📁 Файл: {zip_filename}")
        print("="*60)
        
        return Response(
            json.dumps(response_data, ensure_ascii=False),
            content_type='application/json; charset=utf-8'
        )
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        print("="*60)
        return error_response(f"Ошибка обработки запроса: {str(e)}")

def get_priority_used(language, source_type):
    """Определяет какой приоритет был использован"""
    if language == 'en':
        if source_type == 'manual':
            return "1-й приоритет (Английские ручные)"
        else:
            return "1-й приоритет (Английские автоматические)"
    elif language == 'ru':
        if source_type == 'manual':
            return "2-й приоритет (Русские ручные)"
        else:
            return "2-й приоритет (Русские автоматические)"
    else:
        if source_type == 'manual':
            return f"3-й приоритет (Язык видео: {language}, ручные)"
        else:
            return f"4-й приоритет (Доступный язык: {language}, автоматические)"

@app.route('/download/<filename>')
def download_file(filename):
    """Скачивание готового файла"""
    try:
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        if not os.path.exists(filepath):
            return error_response("Файл не найден или устарел")
        
        print(f"📤 Отправка файла: {filename}")
        
        return Response(
            open(filepath, 'rb').read(),
            mimetype='application/zip',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
        
    except Exception as e:
        print(f"❌ Ошибка скачивания: {e}")
        return error_response("Ошибка при скачивании файла")

@app.route('/status')
def status():
    """Статус сервиса"""
    cleanup_old_files()
    
    # Подсчитываем файлы
    files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.zip')]
    total_size = sum(os.path.getsize(os.path.join(UPLOAD_FOLDER, f)) for f in files) / 1024
    
    # Информация о доступности файлов авторизации
    cookies_exists = os.path.exists(COOKIES_FILE)
    token_exists = os.path.exists(LOCAL_TOKEN_FILE) or bool(os.environ.get('OAUTH_TOKEN'))
    
    # Проверяем, работают ли файлы авторизации
    auth_status = "⚠️ Не проверено"
    if cookies_exists:
        try:
            with open(COOKIES_FILE, 'r') as f:
                content = f.read()
                if 'youtube.com' in content:
                    auth_status = "✅ Cookies файл валиден"
                else:
                    auth_status = "❌ Cookies файл не содержит данные YouTube"
        except:
            auth_status = "❌ Ошибка чтения cookies файла"
    
    return json.dumps({
        'status': 'online',
        'files_count': len(files),
        'total_size_kb': round(total_size, 2),
        'cookies_file': cookies_exists,
        'oauth_token': token_exists,
        'auth_status': auth_status,
        'upload_folder': UPLOAD_FOLDER,
        'priority_system': {
            '1': 'Английский (ручные → авто)',
            '2': 'Русский (ручные → авто)',
            '3': 'Язык видео (определяется по заголовку)',
            '4': 'Любой доступный язык'
        },
        'api_endpoints': {
            'GET': '/download?url=YOUTUBE_URL',
            'POST': '/download (JSON: {"url": "YOUTUBE_URL"})'
        },
        'anti_bot_features': {
            'random_user_agent': True,
            'retry_system': True,
            'multiple_auth_methods': True,
            'embed_fallback': True
        }
    })

@app.route('/test')
def test_page():
    """Тестовая страница с примерами ссылок"""
    cleanup_old_files()
    
    test_videos = [
        {'id': 'dQw4w9WgXcQ', 'title': 'Rick Astley - Never Gonna Give You Up'},
        {'id': '9bZkp7q19f0', 'title': 'PSY - GANGNAM STYLE'},
        {'id': 'kJQP7kiw5Fk', 'title': 'Luis Fonsi - Despacito ft. Daddy Yankee'},
        {'id': 'JGwWNGJdvx8', 'title': 'Ed Sheeran - Shape of You'},
        {'id': 'clLNRRHTWP4', 'title': 'Тестовое видео (с блокировкой)'}
    ]
    
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>🎬 Test YouTube Subtitles</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .video { margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
            .btn { display: inline-block; padding: 10px 20px; margin: 5px; color: white; text-decoration: none; border-radius: 5px; }
            .btn-get { background: #007bff; }
            .btn-get:hover { background: #0056b3; }
            .btn-post { background: #28a745; }
            .btn-post:hover { background: #1e7e34; }
            .status { padding: 5px 10px; border-radius: 3px; margin-left: 10px; }
            .status-working { background: #d4edda; color: #155724; }
            .status-blocked { background: #f8d7da; color: #721c24; }
        </style>
    </head>
    <body>
        <h1>🎬 Тестовые видео для скачивания субтитров</h1>
        <p>Нажмите на ссылку для скачивания субтитров:</p>
    '''
    
    for video in test_videos:
        blocked = video['id'] == 'clLNRRHTWP4'
        status_class = 'status-blocked' if blocked else 'status-working'
        status_text = '⚠️ Может быть заблокировано' if blocked else '✅ Обычно работает'
        
        html += f'''
        <div class="video">
            <h3>{video['title']} <span class="status {status_class}">{status_text}</span></h3>
            <p>ID: {video['id']}</p>
            <a class="btn btn-get" href="/download?url=https://www.youtube.com/watch?v={video['id']}" target="_blank">
                📥 Скачать субтитры (GET)
            </a>
            <button class="btn btn-post" onclick='testPost("{video['id']}")'>
                📨 Тест POST запроса
            </button>
        </div>
        '''
    
    html += '''
        <script>
        function testPost(videoId) {
            fetch('/download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    url: 'https://www.youtube.com/watch?v=' + videoId
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert('✅ Успешно! Скачать файл: ' + data.download_url);
                    window.open(data.download_url, '_blank');
                } else {
                    alert('❌ Ошибка: ' + data.error);
                }
            })
            .catch(error => {
                alert('❌ Ошибка сети: ' + error);
            });
        }
        </script>
        <p><a href="/">← Назад на главную</a></p>
    </body>
    </html>
    '''
    
    return html

@app.route('/refresh_cookies')
def refresh_cookies_info():
    """Информация о обновлении cookies"""
    return '''
    <!DOCTYPE html>
    <html>
    <head><title>Обновление Cookies</title></head>
    <body>
        <h1>🔄 Как обновить cookies.txt</h1>
        <p>Для работы с YouTube без блокировок нужно:</p>
        <ol>
            <li>Установите расширение <a href="https://chrome.google.com/webstore/detail/get-cookiestxt/bgaddhkoddajcdgocldbbfleckgcbcid" target="_blank">Get cookies.txt</a> в Chrome</li>
            <li>Зайдите на <a href="https://youtube.com" target="_blank">YouTube.com</a> и авторизуйтесь</li>
            <li>Нажмите на расширение и экспортируйте cookies в файл cookies.txt</li>
            <li>Загрузите этот файл на сервер (в Render нужно добавить через Dashboard → Environment)</li>
        </ol>
        <p><a href="/">← Назад</a></p>
    </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n" + "="*70)
    print(f"🚀 Запускаю YouTube Subtitles Downloader")
    print(f"📁 Папка для файлов: {UPLOAD_FOLDER}")
    print(f"🍪 Cookies файл: {COOKIES_FILE} ({'найден' if os.path.exists(COOKIES_FILE) else 'не найден'})")
    print(f"🔑 OAuth Token: {'найден' if os.path.exists(LOCAL_TOKEN_FILE) or os.environ.get('OAUTH_TOKEN') else 'не найден'})")
    print(f"\n🎯 СИСТЕМА ПРИОРИТЕТОВ ЯЗЫКОВ:")
    print(f"   1. Английский (ручные → автоматические)")
    print(f"   2. Русский (ручные → автоматические)")
    print(f"   3. Язык видео (определяется по заголовку)")
    print(f"   4. Любой доступный язык")
    print(f"\n🛡️ АНТИ-БЛОК СИСТЕМА:")
    print(f"   ✓ Рандомизация User-Agent")
    print(f"   ✓ Многоуровневая система ретраев")
    print(f"   ✓ Альтернативные методы доступа")
    print(f"   ✓ Поддержка разных клиентов (android/web/ios/tv)")
    print(f"\n🔧 Порт: {port}")
    print(f"\n🌐 Доступные эндпоинты:")
    print(f"   GET  /download?url=YOUTUBE_URL")
    print(f"   POST /download (JSON)")
    print(f"   GET  /status")
    print(f"   GET  /test")
    print(f"   GET  /refresh_cookies")
    print("="*70 + "\n")
    app.run(host='0.0.0.0', port=port)