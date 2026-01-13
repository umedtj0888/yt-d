import os
import json
import logging
import yt_dlp
from flask import Flask, request, Response, redirect

app = Flask(__name__)

# Файл, куда мы сохраним токен
TOKEN_FILE = 'oauth_token.txt'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    # Если токен уже есть, покажем его
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as f:
            token = f.read().strip()
        return f"""
        <h1>✅ Токен получен!</h1>
        <p>Скопируйте этот токен и вставьте в ваш основной код:</p>
        <textarea style="width:100%; height:100px;">{token}</textarea>
        <p><a href="/reset">Сбросить и получить новый</a></p>
        """

    # Если токена нет, показываем кнопку старта
    return """
    <h1>Получение OAuth Токена для Render.com</h1>
    <p>Нажмите кнопку ниже. Система сгенерирует ссылку.</p>
    <form action="/start_auth" method="post">
        <button type="submit" style="padding: 20px; font-size: 20px; background: #FF0000; color: white; border: none; cursor: pointer;">
            1. Сгенерировать ссылку
        </button>
    </form>
    """

@app.route('/start_auth', methods=['POST'])
def start_auth():
    """
    Запускает процесс авторизации.
    Нам нужно сымитировать браузер, чтобы получить oauth_refreshtoken
    """
    logger.info("Starting auth process...")
    
    # Настраиваем yt-dlp на Android (самый надежный способ)
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'client_id': 'android',
        # Флаг, который заставляет yt-dlp начать процесс OAuth
        # В рамках Web App это может быть сложно, но попробуем через extract_flat
        # ОДНАКО, на самом деле лучший способ - это использовать ссылку
    }
    
    # ВАЖНО: Мы не можем запустить интерактивный режим в Flask.
    # Но мы можем показать пользователю готовую ссылку для входа.
    
    # Генерируем ссылку для YouTube OAuth (Android Client)
    # client_id для YouTube Android известен:
    # 539896302262-j0f2hp8p8h8tgag3j0n5g9h6o9k0o8q7.apps.googleusercontent.com (пример)
    
    # Мы используем упрощенный подход: даем пользователю ссылку для получения кода
    auth_url = (
        "https://www.youtube.com/o/oauth2/auth?"
        "client_id=674416935537-uiquphecfgtt7v93gdncdppar8jsnu5g.apps.googleusercontent.com&"
        "redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob&"
        "response_type=code&"
        "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.force-ssl"
    )
    
    html = f"""
    <h2>Шаг 2: Авторизация</h2>
    <ol>
        <li><a href="{auth_url}" target="_blank"><b>Нажмите сюда, чтобы открыть Google</b></a></li>
        <li>Войдите в аккаунт (если попросит).</li>
        <li>В конце вам выдадут <b>Код подтверждения</b> (длинная строка букв и цифр).</li>
    </ol>
    
    <form action="/submit_code" method="post">
        <label>Вставьте код сюда:</label><br>
        <input type="text" name="auth_code" style="width: 100%; height: 50px;" placeholder="4/0AX..." required>
        <br><br>
        <button type="submit" style="padding: 15px; font-size: 18px;">2. Обменять код на Токен</button>
    </form>
    """
    return html

@app.route('/submit_code', methods=['POST'])
def submit_code():
    """
    Эта функция берет код от Google и меняет его на oauth_refresh_token
    """
    code = request.form.get('auth_code')
    if not code:
        return "Ошибка: Код не введен"

    logger.info(f"Received code, exchanging for token...")
    
    # Используем yt-dlp для обмена кода на токен
    # Создаем временный файл конфигурации, чтобы yt-dlp не ругался
    
    try:
        # Внимание: здесь мы используем трюк с yt_dlp.YoutubeDL
        # Но стандартный yt-dlp требует интерактивного режима или cookies.
        # Самый надежный способ обмена кода - использовать запрос к Google API напрямую
        # через yt_dlp utils или сам Python.
        
        # Простой способ через yt_dlp:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            # Нам нужно сообщить yt-dlp, что мы уже получили код
            # Но yt-dlp не умеет принимать code напрямую для обмена, он запускает свой цикл.
        }
        
        # Поэтому используем прямой запрос к Google Token Endpoint
        # Это то, что делает yt-dlp под капотом.
        import urllib.request
        import urllib.parse
        
        # Данные для обмена
        data = urllib.parse.urlencode({
            'client_id': '674416935537-uiquphecfgtt7v93gdncdppar8jsnu5g.apps.googleusercontent.com',
            'client_secret': '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T',
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob'
        }).encode('utf-8')
        
        req = urllib.request.Request(
            'https://oauth2.googleapis.com/token',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        with urllib.request.urlopen(req) as response:
            response_data = json.loads(response.read())
            
        if 'refresh_token' in response_data:
            token = response_data['refresh_token']
            
            # Сохраняем в файл
            with open(TOKEN_FILE, 'w') as f:
                f.write(token)
            
            return redirect('/')
        else:
            return f"Ошибка: Токен не получен. Ответ Google: {response_data}"
            
    except Exception as e:
        return f"Ошибка при обмене кода: {e}"

@app.route('/reset')
def reset():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return redirect('/')

if __name__ == '__main__':
    # Render требует запуск на 0.0.0.0
    app.run(host='0.0.0.0', port=10000)
