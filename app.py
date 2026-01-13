from flask import Flask, request, jsonify
import yt_dlp
import os
import tempfile

app = Flask(__name__)

# Путь к файлу cookies.txt (должен быть в корне проекта)
COOKIES_PATH = os.path.join(os.path.dirname(__file__), 'cookies.txt')

def get_subtitles_text(video_id, lang='en'):
    """
    Скачивает субтитры для video_id на указанном языке (по умолчанию 'en')
    и возвращает текст. Если субтитров нет - возвращает None.
    """
    video_url = f'https://www.youtube.com/watch?v={video_id}'

    # Создаем временную директорию для файла субтитров
    with tempfile.TemporaryDirectory() as tmpdir:
        ydl_opts = {
            'cookiefile': COOKIES_PATH if os.path.exists(COOKIES_PATH) else None,
            'writesubtitles': True,        # Скачивать обычные субтитры
            'writeautomaticsub': True,     # Скачивать автогенерированные субтитры
            'subtitleslangs': [lang],      # Язык субтитров (можно изменить на 'ru' и т.д.)
            'skip_download': True,         # Не скачивать видео
            'outtmpl': os.path.join(tmpdir, 'sub'),  # Шаблон имени файла
            'quiet': True,
            'no_warnings': True,
            # Критически важные параметры для обхода блокировки
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],  # Имитация разных клиентов
                    'player_skip': ['configs'],            # Пропуск лишних запросов
                }
            },
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'throttled_rate': '1M',  # Ограничение скорости (1 Мбит/с)
            'sleep_interval_requests': 1,  # Пауза между запросами
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Извлекаем информацию (файл субтитров будет создан в tmpdir)
                info = ydl.extract_info(video_url, download=True)
                
                # Ищем скачанный файл субтитров
                expected_filename = os.path.join(tmpdir, f'sub.{lang}.vtt')
                if os.path.exists(expected_filename):
                    with open(expected_filename, 'r', encoding='utf-8') as f:
                        return f.read()  # Возвращаем ВЕСЬ текст из файла
                else:
                    # Если .vtt нет, ищем .srt
                    expected_filename_srt = os.path.join(tmpdir, f'sub.{lang}.srt')
                    if os.path.exists(expected_filename_srt):
                        with open(expected_filename_srt, 'r', encoding='utf-8') as f:
                            return f.read()
                    else:
                        print(f"[DEBUG] Файл субтитров не найден в {tmpdir}. Доступные языки: {list(info.get('subtitles', {}).keys())}")
                        return None
        except Exception as e:
            print(f"[ERROR] Ошибка yt-dlp для {video_id}: {str(e)}")
            return None
            
def clean_subtitles(vtt_text):
    return ' '.join([line.strip() for line in vtt_text.split('\n') 
                    if line.strip() and not any(x in line for x in ['WEBVTT', '-->', 'Kind:', 'Language:'])])

@app.route('/')
def index():
    return jsonify({
        'message': 'Сервис получения субтитров YouTube',
        'usage': 'GET /subtitles/<video_id>?lang=<код_языка>',
        'example': '/subtitles/dQw4w9WgXcQ?lang=en'
    })

@app.route('/subtitles/<video_id>')
def subtitles(video_id):
    # Получаем язык из параметра запроса (по умолчанию 'en')
    lang = request.args.get('lang', 'en')
    
    # Получаем текст субтитров
    subtitles_text = get_subtitles_text(video_id, lang)
    
    if subtitles_text is not None:
        cleaned_text = clean_subtitles(subtitles_text)
        # Возвращаем успешный ответ с текстом
        return jsonify({
            'success': True,
            'video_id': video_id,
            'language': lang,
            'subtitles': cleaned_text,
            'format': 'vtt/srt (raw text)'
        })
    else:
        # Возвращаем ошибку
        return jsonify({
            'success': False,
            'video_id': video_id,
            'language': lang,
            'error': 'Субтитры не найдены или произошла ошибка при получении'
        }), 404

# Точка входа для Render
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)