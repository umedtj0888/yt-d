from flask import Flask, request, Response
import json
import os
import time
import re
import tempfile
import uuid
import zipfile
import urllib.request
import urllib.parse
import random
from http.cookiejar import CookieJar

app = Flask(__name__)
UPLOAD_FOLDER = 'subtitles'
COOKIES_FILE = 'cookies.txt'
LOCAL_TOKEN_FILE = 'oauth_token.txt'

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
    """Быстро получает информацию о видео через oEmbed API"""
    try:
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return {
                'title': data.get('title', 'Unknown Video'),
                'author_name': data.get('author_name', 'Unknown Author')
            }
    except Exception as e:
        print(f"⚠️ Ошибка получения информации о видео: {e}")
        return {'title': 'Unknown Video', 'author_name': 'Unknown Author'}

def get_random_user_agent():
    """Генерирует случайный User-Agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (Linux; Android 10; SM-G960F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    ]
    return random.choice(user_agents)

def get_subtitles_directly(video_id):
    """Прямое скачивание субтитров через альтернативные методы"""
    print("🔄 Пробуем прямой метод скачивания субтитров...")
    
    # Метод 1: Попробуем получить через встраиваемую страницу
    try:
        embed_url = f"https://www.youtube.com/embed/{video_id}"
        user_agent = get_random_user_agent()
        
        # Создаем opener с cookies
        cj = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        
        # Загружаем cookies из файла если есть
        if os.path.exists(COOKIES_FILE):
            try:
                import http.cookiejar
                cj.load(COOKIES_FILE)
                print("🍪 Загружены cookies из файла")
            except:
                pass
        
        # Пробуем получить страницу
        req = urllib.request.Request(embed_url)
        req.add_header('User-Agent', user_agent)
        req.add_header('Accept-Language', 'en-US,en;q=0.9')
        req.add_header('Referer', 'https://www.youtube.com/')
        req.add_header('Origin', 'https://www.youtube.com')
        
        try:
            response = opener.open(req, timeout=15)
            html_content = response.read().decode('utf-8', errors='ignore')
            
            # Ищем субтитры в HTML
            subtitle_patterns = [
                r'"subtitles"\s*:\s*({[^}]+})',
                r'"captionTracks"\s*:\s*(\[[^\]]+\])',
                r'"playerCaptionsTracklistRenderer"\s*:\s*({[^}]+})'
            ]
            
            for pattern in subtitle_patterns:
                match = re.search(pattern, html_content)
                if match:
                    print("✅ Найдены субтитры в HTML")
                    try:
                        subtitles_data = json.loads(match.group(1))
                        return extract_subtitles_from_data(subtitles_data)
                    except:
                        continue
            
        except Exception as e:
            print(f"⚠️ Ошибка при загрузке embed страницы: {e}")
    except Exception as e:
        print(f"⚠️ Ошибка в методе 1: {e}")
    
    # Метод 2: Попробуем через get_video_info API
    try:
        info_url = f"https://www.youtube.com/get_video_info?video_id={video_id}&el=embedded&ps=default&eurl=&gl=US&hl=en"
        req = urllib.request.Request(info_url)
        req.add_header('User-Agent', get_random_user_agent())
        
        with urllib.request.urlopen(req, timeout=15) as response:
            info_content = response.read().decode('utf-8')
            info_dict = urllib.parse.parse_qs(info_content)
            
            if 'player_response' in info_dict:
                player_response = json.loads(info_dict['player_response'][0])
                
                # Ищем субтитры
                captions = player_response.get('captions', {})
                if captions:
                    player_captions = captions.get('playerCaptionsTracklistRenderer', {})
                    if player_captions:
                        caption_tracks = player_captions.get('captionTracks', [])
                        if caption_tracks:
                            print(f"✅ Найдено {len(caption_tracks)} треков субтитров")
                            # Ищем английские субтитры
                            for track in caption_tracks:
                                if track.get('languageCode') == 'en' and 'baseUrl' in track:
                                    return download_subtitle_track(track['baseUrl'])
    
    except Exception as e:
        print(f"⚠️ Ошибка в методе 2: {e}")
    
    # Метод 3: Попробуем через yt.innertube API
    try:
        innertube_url = "https://www.youtube.com/youtubei/v1/player"
        headers = {
            'User-Agent': get_random_user_agent(),
            'Content-Type': 'application/json',
            'Accept': '*/*',
            'Origin': 'https://www.youtube.com',
            'Referer': f'https://www.youtube.com/watch?v={video_id}',
            'X-YouTube-Client-Name': '1',
            'X-YouTube-Client-Version': '2.20240115.01.00'
        }
        
        data = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240115.01.00",
                    "hl": "en",
                    "gl": "US"
                }
            },
            "videoId": video_id,
            "captionParams": {}
        }
        
        req = urllib.request.Request(innertube_url, json.dumps(data).encode(), headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode())
            
            # Ищем субтитры
            captions = result.get('captions', {})
            if captions:
                player_captions = captions.get('playerCaptionsTracklistRenderer', {})
                if player_captions:
                    caption_tracks = player_captions.get('captionTracks', [])
                    if caption_tracks:
                        print(f"✅ Найдено {len(caption_tracks)} треков субтитров через innertube API")
                        # Ищем английские субтитры
                        for track in caption_tracks:
                            if track.get('languageCode') == 'en' and 'baseUrl' in track:
                                return download_subtitle_track(track['baseUrl'])
    
    except Exception as e:
        print(f"⚠️ Ошибка в методе 3: {e}")
    
    return None, None

def extract_subtitles_from_data(data):
    """Извлекает субтитры из данных"""
    try:
        if isinstance(data, dict):
            # Ищем треки субтитров
            caption_tracks = data.get('captionTracks', [])
            if not caption_tracks and 'captionTracks' in str(data):
                # Пробуем найти в строковом представлении
                match = re.search(r'"captionTracks"\s*:\s*(\[[^\]]+\])', str(data))
                if match:
                    try:
                        caption_tracks = json.loads(match.group(1))
                    except:
                        pass
            
            if caption_tracks:
                print(f"📊 Найдено {len(caption_tracks)} треков субтитров")
                # Ищем английские субтитры
                for track in caption_tracks:
                    if isinstance(track, dict):
                        if track.get('languageCode') == 'en' and 'baseUrl' in track:
                            print(f"✅ Найден английский трек субтитров")
                            return download_subtitle_track(track['baseUrl'])
                
                # Если английских нет, берем первый доступный
                for track in caption_tracks:
                    if isinstance(track, dict) and 'baseUrl' in track:
                        lang = track.get('languageCode', 'unknown')
                        print(f"📝 Берем субтитры на языке: {lang}")
                        return download_subtitle_track(track['baseUrl'])
    except Exception as e:
        print(f"⚠️ Ошибка извлечения субтитров: {e}")
    
    return None, None

def download_subtitle_track(url):
    """Скачивает трек субтитров по URL"""
    try:
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.youtube.com/',
            'Origin': 'https://www.youtube.com'
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read().decode('utf-8', errors='ignore')
            
            # Определяем формат по URL
            if '.json' in url or 'fmt=json' in url:
                return clean_json_subtitles(content), 'json'
            elif '.vtt' in url or 'fmt=vtt' in url:
                return clean_vtt_subtitles(content), 'vtt'
            elif '.srt' in url or 'fmt=srt' in url:
                return clean_srt_subtitles(content), 'srt'
            else:
                # Пробуем определить формат
                if content.strip().startswith('{'):
                    return clean_json_subtitles(content), 'json'
                elif 'WEBVTT' in content:
                    return clean_vtt_subtitles(content), 'vtt'
                elif '-->' in content:
                    return clean_srt_subtitles(content), 'srt'
                else:
                    return clean_generic_subtitles(content), 'unknown'
    
    except Exception as e:
        print(f"⚠️ Ошибка скачивания трека: {e}")
        return None, None

def clean_json_subtitles(content):
    """Очищает субтитры в формате JSON"""
    try:
        data = json.loads(content)
        text_parts = []
        
        # Проверяем разные структуры JSON
        if 'events' in data:
            for event in data['events']:
                if 'segs' in event:
                    for seg in event['segs']:
                        if 'utf8' in seg:
                            text = seg['utf8'].strip()
                            if text and not text.startswith('['):
                                text_parts.append(text)
        
        result = ' '.join(text_parts).strip()
        return result if len(result) > 10 else None
    except:
        return None

def clean_vtt_subtitles(content):
    """Очищает субтитры в формате VTT"""
    lines = content.split('\n')
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('WEBVTT') or line.startswith('NOTE') or '-->' in line:
            continue
        line = re.sub(r'<[^>]+>', '', line)
        if line:
            text_lines.append(line)
    result = ' '.join(text_lines).strip()
    return result if len(result) > 10 else None

def clean_srt_subtitles(content):
    """Очищает субтитры в формате SRT"""
    lines = content.split('\n')
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or '-->' in line:
            continue
        line = re.sub(r'<[^>]+>', '', line)
        if line:
            text_lines.append(line)
    result = ' '.join(text_lines).strip()
    return result if len(result) > 10 else None

def clean_generic_subtitles(content):
    """Очищает субтитры в неизвестном формате"""
    lines = content.split('\n')
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or '-->' in line or line.startswith('WEBVTT'):
            continue
        line = re.sub(r'<[^>]+>', '', line)
        line = re.sub(r'{\\.*?}', '', line)
        if line and len(line) > 2:
            text_lines.append(line)
    result = ' '.join(text_lines).strip()
    return result if len(result) > 10 else None

def download_subtitles_simple(video_id):
    """Упрощенная функция скачивания субтитров"""
    print(f"🔍 Поиск субтитров для видео: {video_id}")
    
    # Получаем информацию о видео
    video_info = get_video_info(video_id)
    
    # Пробуем получить субтитры разными методами
    subtitles_text, subtitles_format = get_subtitles_directly(video_id)
    
    if subtitles_text:
        print(f"✅ Субтитры найдены, формат: {subtitles_format}")
        return {
            'title': video_info['title'],
            'author': video_info['author_name'],
            'subtitles': subtitles_text,
            'video_id': video_id,
            'language': 'en',  # По умолчанию считаем английскими
            'source_type': 'direct'
        }
    else:
        print("❌ Субтитры не найдены")
        return None

def create_zip_file(video_title, subtitles_text, video_id, language='en'):
    """Создает ZIP файл с субтитрами"""
    clean_title = re.sub(r'[<>:"/\\|?*]', '_', video_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    if len(clean_title) > 50:
        clean_title = clean_title[:50]
    
    zip_filename = f"{video_id}_{uuid.uuid4().hex[:6]}.zip"
    zip_filepath = os.path.join(UPLOAD_FOLDER, zip_filename)
    
    metadata = f"""Видео: {video_title}
Язык: {language}
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
    """Главная страница"""
    cleanup_old_files()
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>YouTube Subtitles Downloader</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
            pre { background: #f4f4f4; padding: 15px; border-radius: 5px; }
            .btn { display: inline-block; padding: 10px 20px; background: #007bff; color: white; text-decoration: none; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>🎬 YouTube Subtitles Downloader</h1>
        <p>Простой сервис для скачивания субтитров с YouTube</p>
        
        <h2>📋 Использование:</h2>
        <pre>
GET /download?url=https://youtube.com/watch?v=VIDEO_ID
        </pre>
        
        <h2>📝 Пример:</h2>
        <pre>
<a href="/download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ">
    /download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ
</a>
        </pre>
        
        <a href="/test" class="btn">🎬 Тестовая страница</a>
    </body>
    </html>
    '''

@app.route('/download', methods=['GET', 'POST'])
def download_subtitles_route():
    """Основной эндпоинт для скачивания субтитров"""
    cleanup_old_files()
    
    try:
        youtube_url = None
        
        if request.method == 'GET':
            youtube_url = request.args.get('url')
            if not youtube_url:
                return error_response("Используйте параметр ?url= с ссылкой на YouTube видео")
        
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
        print("="*60)
        
        result = download_subtitles_simple(video_id)
        
        if not result:
            return error_response("Субтитры не найдены для этого видео")
        
        zip_filename, clean_title = create_zip_file(
            result['title'], 
            result['subtitles'], 
            video_id,
            result.get('language', 'en')
        )
        
        response_data = {
            'success': True,
            'video_title': result['title'],
            'author': result['author'],
            'video_id': video_id,
            'download_url': f"{request.host_url}download/{zip_filename}",
            'filename': f"{clean_title}.zip",
            'language': result.get('language', 'en'),
            'method': result.get('source_type', 'direct')
        }
        
        print(f"\n✅ Готово: {result['title']}")
        print(f"📁 Файл: {zip_filename}")
        print("="*60)
        
        return Response(
            json.dumps(response_data, ensure_ascii=False),
            content_type='application/json; charset=utf-8'
        )
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        return error_response(f"Ошибка обработки: {str(e)}")

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

@app.route('/test')
def test_page():
    """Тестовая страница"""
    test_videos = [
        {'id': 'dQw4w9WgXcQ', 'title': 'Rick Astley - Never Gonna Give You Up'},
        {'id': '9bZkp7q19f0', 'title': 'PSY - GANGNAM STYLE'},
        {'id': 'kJQP7kiw5Fk', 'title': 'Luis Fonsi - Despacito ft. Daddy Yankee'},
    ]
    
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Тест YouTube Subtitles</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .video { margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
            .btn { display: inline-block; padding: 10px 20px; background: #28a745; color: white; text-decoration: none; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>🎬 Тестовые видео</h1>
    '''
    
    for video in test_videos:
        html += f'''
        <div class="video">
            <h3>{video['title']}</h3>
            <p>ID: {video['id']}</p>
            <a class="btn" href="/download?url=https://www.youtube.com/watch?v={video['id']}" target="_blank">
                📥 Скачать субтитры
            </a>
        </div>
        '''
    
    html += '''
        <p><a href="/">← Назад</a></p>
    </body>
    </html>
    '''
    
    return html

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n🚀 Запуск сервиса на порту {port}")
    app.run(host='0.0.0.0', port=port)