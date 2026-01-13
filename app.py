from flask import Flask, request, jsonify
import yt_dlp
import os

app = Flask(__name__)

# Путь к файлу cookies.txt (Render загрузит его вместе с кодом)
COOKIES_PATH = os.path.join(os.path.dirname(__file__), 'cookies.txt')

def get_subtitles(video_id):
    video_url = f'https://www.youtube.com/watch?v={video_id}'
    print(f"yt-dlp version: {yt_dlp.version.__version__}")
    print(f"yt-dlp version: {yt_dlp.version.__version__}")
    print(f"yt-dlp version: {yt_dlp.version.__version__}")

    # Ключевые параметры для обхода блокировки
    ydl_opts = {
        'cookiefile': COOKIES_PATH,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'ru'],  # Языки: английский, русский
        'skip_download': True,
        'quiet': True,
        # Обходные параметры для имитации легитимного клиента
        'extractor_args': {'youtube': {'player_client': ['android']}},
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        'throttled_rate': '100K',  # Ограничение скорости скачивания
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            # Получаем список доступных субтитров
            subtitles = info.get('subtitles', {}) or info.get('automatic_captions', {})
            return {'video_id': video_id, 'subtitles': subtitles}
    except Exception as e:
        # Логируем ошибку для отладки на Render
        print(f"[ERROR] Failed to get subtitles for {video_id}: {str(e)}")
        return None

@app.route('/')
def index():
    return jsonify({'message': 'Send a GET request to /subtitles/<video_id>'})

@app.route('/subtitles/<video_id>')
def subtitles(video_id):
    result = get_subtitles(video_id)
    if result:
        return jsonify(result)
    else:
        return jsonify({'error': 'Could not fetch subtitles. The service might be temporarily blocked.'}), 503

# Точка входа для Render
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)