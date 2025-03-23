"""
这是一个文字转语音(TTS)的网络服务器程序

这个程序可以把输入的文字变成语音，就像你的老师读课文一样。
它支持多种不同的"引擎"(就是不同的发声方式)，比如Azure、OpenAI等。
你可以通过浏览器访问这个服务，输入文字，然后听到电脑读出来的声音。

主要功能：
1. 把文字转换成语音
2. 支持多种语音引擎
3. 可以选择不同的声音
4. 提供网页界面让用户操作

作者：RealtimeTTS团队
"""

if __name__ == "__main__":
    print("Starting server")
    import logging

    # 是否开启详细日志记录
    DEBUG_LOGGING = False

    # 设置日志级别。日志就像是程序的笔记本，记录程序运行时发生的事情
    if DEBUG_LOGGING:
        logging.basicConfig(level=logging.DEBUG)  # 详细模式，记录所有信息
    else:
        logging.basicConfig(level=logging.WARNING)  # 简略模式，只记录警告和错误


# 导入所需的语音引擎
# 这就像是我们邀请不同的"朗读员"来帮我们读文字
from RealtimeTTS import (
    TextToAudioStream,  # 文字转音频流的主要工具
    AzureEngine,        # 微软Azure的语音服务
    ElevenlabsEngine,   # ElevenLabs的语音服务
    SystemEngine,       # 系统自带的语音服务
    CoquiEngine,        # Coqui的语音服务
    OpenAIEngine,       # OpenAI的语音服务
    KokoroEngine        # Kokoro的语音服务
)

# 导入网络服务器需要的工具
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse  # 不同类型的响应
from fastapi.middleware.cors import CORSMiddleware  # 允许跨域请求的工具
from fastapi import FastAPI, Query, Request  # FastAPI框架的核心组件
from fastapi.staticfiles import StaticFiles  # 用于提供静态文件（如JS、CSS）

# 导入其他必要的库
from queue import Queue        # 队列，用于存储音频数据
import threading               # 线程，让程序可以同时做多件事
import logging                 # 日志记录
import uvicorn                 # 网络服务器
import wave                    # 处理WAV音频文件
import io                      # 输入输出流处理
import os                      # 操作系统相关功能

# 设置服务器端口，可以通过环境变量修改，默认是8000
# 端口就像是房子的门牌号，通过它可以找到我们的服务
PORT = int(os.environ.get("TTS_FASTAPI_PORT", 8000))

# 支持的语音引擎列表
# 这些就是我们可以使用的不同"朗读员"
SUPPORTED_ENGINES = [
    "azure",       # 微软的Azure语音服务
    "openai",      # OpenAI的语音服务
    "elevenlabs",  # ElevenLabs的语音服务
    "system",      # 你电脑自带的语音服务
    # "coqui",     # Coqui语音服务（目前被注释掉了，因为它不支持多个查询）
    "kokoro"       # Kokoro语音服务
]

# 更改启动引擎，只需将引擎名称移动到SUPPORTED_ENGINES的第一个位置
START_ENGINE = SUPPORTED_ENGINES[0]

# 浏览器标识符列表，用于判断请求是否来自浏览器
BROWSER_IDENTIFIERS = [
    "mozilla",
    "chrome",
    "safari",
    "firefox",
    "edge",
    "opera",
    "msie",
    "trident",
]

# 允许跨域请求的来源列表
origins = [
    "http://localhost",
    f"http://localhost:{PORT}",
    "http://127.0.0.1",
    f"http://127.0.0.1:{PORT}",
    "https://localhost",
    f"https://localhost:{PORT}",
    "https://127.0.0.1",
    f"https://127.0.0.1:{PORT}",
]

# 用于控制同时播放的语音数量
play_text_to_speech_semaphore = threading.Semaphore(1)
engines = {}  # 存储所有初始化的语音引擎
voices = {}  # 存储每个引擎的可用声音
current_engine = None  # 当前使用的语音引擎
speaking_lock = threading.Lock()  # 控制语音播放的锁
tts_lock = threading.Lock()  # 控制TTS请求的锁
gen_lock = threading.Lock()  # 控制生成的锁


class TTSRequestHandler:
    """
    处理TTS请求的类
    """

    def __init__(self, engine):
        self.engine = engine
        self.audio_queue = Queue()
        self.stream = TextToAudioStream(
            engine, on_audio_stream_stop=self.on_audio_stream_stop, muted=True
        )
        self.speaking = False

    def on_audio_chunk(self, chunk):
        """
        当接收到音频块时，将其放入队列
        """
        self.audio_queue.put(chunk)

    def on_audio_stream_stop(self):
        """
        当音频流停止时，向队列中放入None表示结束
        """
        self.audio_queue.put(None)
        self.speaking = False

    def play_text_to_speech(self, text):
        """
        播放文字转语音
        """
        self.speaking = True
        self.stream.feed(text)
        logging.debug(f"Playing audio for text: {text}")
        print(f'Synthesizing: "{text}"')
        self.stream.play_async(on_audio_chunk=self.on_audio_chunk, muted=True)

    def audio_chunk_generator(self, send_wave_headers):
        """
        生成音频块的生成器
        """
        first_chunk = False
        try:
            while True:
                chunk = self.audio_queue.get()
                if chunk is None:
                    print("Terminating stream")
                    break
                if not first_chunk:
                    if send_wave_headers:
                        print("Sending wave header")
                        yield create_wave_header_for_engine(self.engine)
                    first_chunk = True
                yield chunk
        except Exception as e:
            print(f"Error during streaming: {str(e)}")


# 创建FastAPI应用
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 定义内容安全策略(CSP)，允许'self'作为脚本源
csp = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data:",
    "font-src": "'self' data:",
    "media-src": "'self' blob:",
}
csp_string = "; ".join(f"{key} {value}" for key, value in csp.items())


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    添加安全头的中间件
    """
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = csp_string
    return response


@app.get("/favicon.ico")
async def favicon():
    """
    提供favicon.ico文件
    """
    return FileResponse("static/favicon.ico")


def _set_engine(engine_name):
    """
    设置当前使用的语音引擎
    """
    global current_engine, stream
    if current_engine is None:
        current_engine = engines[engine_name]
    else:
        current_engine = engines[engine_name]

    if voices[engine_name]:
        engines[engine_name].set_voice(voices[engine_name][0].name)


@app.get("/set_engine")
def set_engine(request: Request, engine_name: str = Query(...)):
    """
    设置语音引擎的API
    """
    if engine_name not in engines:
        return {"error": "Engine not supported"}

    try:
        _set_engine(engine_name)
        return {"message": f"Switched to {engine_name} engine"}
    except Exception as e:
        logging.error(f"Error switching engine: {str(e)}")
        return {"error": "Failed to switch engine"}


def is_browser_request(request):
    """
    判断请求是否来自浏览器
    """
    user_agent = request.headers.get("user-agent", "").lower()
    is_browser = any(browser_id in user_agent for browser_id in BROWSER_IDENTIFIERS)
    return is_browser


def create_wave_header_for_engine(engine):
    """
    为引擎创建WAV文件头
    """
    _, _, sample_rate = engine.get_stream_info()

    num_channels = 1
    sample_width = 2
    frame_rate = sample_rate

    wav_header = io.BytesIO()
    with wave.open(wav_header, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(frame_rate)

    wav_header.seek(0)
    wave_header_bytes = wav_header.read()
    wav_header.close()

    # 创建一个具有正确MIME类型的BytesIO，适用于Firefox
    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()


@app.get("/tts")
async def tts(request: Request, text: str = Query(...)):
    """
    文字转语音的API
    """
    with tts_lock:
        request_handler = TTSRequestHandler(current_engine)
        browser_request = is_browser_request(request)

        if play_text_to_speech_semaphore.acquire(blocking=False):
            try:
                threading.Thread(
                    target=request_handler.play_text_to_speech,
                    args=(text,),
                    daemon=True,
                ).start()
            finally:
                play_text_to_speech_semaphore.release()

        return StreamingResponse(
            request_handler.audio_chunk_generator(browser_request),
            media_type="audio/wav",
        )


@app.get("/engines")
def get_engines():
    """
    获取所有支持的语音引擎
    """
    return list(engines.keys())


@app.get("/voices")
def get_voices():
    """
    获取当前引擎的所有声音
    """
    voices_list = []
    for voice in voices[current_engine.engine_name]:
        voices_list.append(voice.name)
    return voices_list


@app.get("/setvoice")
def set_voice(request: Request, voice_name: str = Query(...)):
    """
    设置当前引擎的声音
    """
    print(f"Getting request: {voice_name}")
    if not current_engine:
        print("No engine is currently selected")
        return {"error": "No engine is currently selected"}

    try:
        print(f"Setting voice to {voice_name}")
        current_engine.set_voice(voice_name)
        return {"message": f"Voice set to {voice_name} successfully"}
    except Exception as e:
        print(f"Error setting voice: {str(e)}")
        logging.error(f"Error setting voice: {str(e)}")
        return {"error": "Failed to set voice"}


@app.get("/")
def root_page():
    """
    提供网页界面
    """
    engines_options = "".join(
        [
            f'<option value="{engine}">{engine.title()}</option>'
            for engine in engines.keys()
        ]
    )
    content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Text-To-Speech</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background-color: #f0f0f0;
                    margin: 0;
                    padding: 0;
                }}
                h2 {{
                    color: #333;
                    text-align: center;
                }}
                #container {{
                    width: 80%;
                    margin: 50px auto;
                    background-color: #fff;
                    border-radius: 10px;
                    padding: 20px;
                    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
                }}
                label {{
                    font-weight: bold;
                }}
                select, textarea {{
                    width: 100%;
                    padding: 10px;
                    margin: 10px 0;
                    border: 1px solid #ccc;
                    border-radius: 5px;
                    box-sizing: border-box;
                    font-size: 16px;
                }}
                button {{
                    display: block;
                    width: 100%;
                    padding: 15px;
                    background-color: #007bff;
                    border: none;
                    border-radius: 5px;
                    color: #fff;
                    font-size: 16px;
                    cursor: pointer;
                    transition: background-color 0.3s;
                }}
                button:hover {{
                    background-color: #0056b3;
                }}
                audio {{
                    width: 80%;
                    margin: 10px auto;
                    display: block;
                }}
            </style>
        </head>
        <body>
            <div id="container">
                <h2>Text to Speech</h2>
                <label for="engine">Select Engine:</label>
                <select id="engine">
                    {engines_options}
                </select>
                <label for="voice">Select Voice:</label>
                <select id="voice">
                    <!-- Options will be dynamically populated by JavaScript -->
                </select>
                <textarea id="text" rows="4" cols="50" placeholder="Enter text here..."></textarea>
                <button id="speakButton">Speak</button>
                <audio id="audio" controls></audio> <!-- Hidden audio player -->
            </div>
            <script src="/static/tts.js"></script>
        </body>
    </html>
    """
    return HTMLResponse(content=content)


if __name__ == "__main__":
    print("Initializing TTS Engines")

    for engine_name in SUPPORTED_ENGINES:
        if "azure" == engine_name:
            azure_api_key = os.environ.get("AZURE_SPEECH_KEY")
            azure_region = os.environ.get("AZURE_SPEECH_REGION")
            if azure_api_key and azure_region:
                print("Initializing azure engine")
                engines["azure"] = AzureEngine(azure_api_key, azure_region)

        if "elevenlabs" == engine_name:
            elevenlabs_api_key = os.environ.get("ELEVENLABS_API_KEY")
            if elevenlabs_api_key:
                print("Initializing elevenlabs engine")
                engines["elevenlabs"] = ElevenlabsEngine(elevenlabs_api_key)

        if "system" == engine_name:
            print("Initializing system engine")
            engines["system"] = SystemEngine()

        if "coqui" == engine_name:
            print("Initializing coqui engine")
            engines["coqui"] = CoquiEngine()

        if "kokoro" == engine_name:
            print("Initializing kokoro engine")
            engines["kokoro"] = KokoroEngine()

        if "openai" == engine_name:
            print("Initializing openai engine")
            engines["openai"] = OpenAIEngine()

    for _engine in engines.keys():
        print(f"Retrieving voices for TTS Engine {_engine}")
        try:
            voices[_engine] = engines[_engine].get_voices()
        except Exception as e:
            voices[_engine] = []
            logging.error(f"Error retrieving voices for {_engine}: {str(e)}")

    _set_engine(START_ENGINE)

    print("Server ready")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
