from flask import Flask, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable
import os

app = Flask(__name__)

def get_subtitles_logic(video_id):
    try:
        # Создаем экземпляр API
        ytt_api = YouTubeTranscriptApi()
        
        # Получаем список доступных субтитров
        transcript_list = ytt_api.list(video_id)
        print(transcript_list)
        
        # Сначала пытаемся найти русские субтитры
        try:
            transcript = transcript_list.find_transcript(['ru'])
        except NoTranscriptFound:
            # Если русских нет, ищем английские
            transcript = transcript_list.find_transcript(['en'])
        
        # Получаем данные субтитров
        subtitles_data = transcript.fetch()
        
        return {
            'status': 'success',
            'video_id': video_id,
            'data': subtitles_data
        }
    except TranscriptsDisabled:
        return {'status': 'error', 'message': 'Субтитры отключены для этого видео.'}
    except NoTranscriptFound:
        return {'status': 'error', 'message': 'Субтитры на указанных языках (ru, en) не найдены.'}
    except VideoUnavailable:
        return {'status': 'error', 'message': 'Видео недоступно (удалено или приватное).'}
    except Exception as e:
        return {'status': 'error', 'message': f'Непредвиденная ошибка: {str(e)}'}

@app.route('/')
def index():
    return jsonify({
        'message': 'YouTube Subtitles API is running',
        'usage': '/subtitles/<video_id>'
    })

@app.route('/subtitles/<video_id>')
def subtitles(video_id):
    result = get_subtitles_logic(video_id)
    
    if result['status'] == 'success':
        return jsonify(result)
    else:
        # Возвращаем ошибку с соответствующим кодом
        status_code = 404 if "не найдены" in result['message'] else 400
        return jsonify(result), status_code

if __name__ == '__main__':
    # Порт для Render
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)