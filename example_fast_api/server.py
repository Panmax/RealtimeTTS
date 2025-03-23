import argparse
import os

'''
这是一个文字转语音的服务器程序！
它可以把你输入的文字变成声音，就像有人在读给你听一样。
我们可以使用不同的"引擎"来生成语音，每个引擎都有自己独特的声音特点。
这个程序通过网络接口让你可以输入文字，然后听到对应的语音。
'''

# 命令行参数解析器，帮助我们在启动程序时传入一些设置
parser = argparse.ArgumentParser(description="运行TTS FastAPI服务器。")
parser.add_argument("-p", "--port", type=int, default=int(os.environ.get("TTS_FASTAPI_PORT", 8000)),
                    help="FastAPI服务器运行的端口号（默认：8000或环境变量TTS_FASTAPI_PORT）。")
parser.add_argument('-D', '--debug', action='store_true', help='启用调试日志，详细记录服务器运行情况')

args = parser.parse_args()

# 从命令行参数中获取端口号和是否开启调试模式
PORT = args.port
DEBUG_LOGGING = args.debug

if __name__ == "__main__":
    import logging

    # 根据是否开启调试模式设置日志级别
    # 调试模式下，会显示更多详细信息，帮助开发者找出问题
    if DEBUG_LOGGING:
        logging.basicConfig(level=logging.DEBUG)  # 调试级别：显示所有日志
    else:
        logging.basicConfig(level=logging.WARNING)  # 警告级别：只显示警告和错误

if __name__ == "__main__":
    print(f"在端口 {PORT} 上启动服务器")

# 导入文字转语音相关的工具
from RealtimeTTS import (
    TextToAudioStream,  # 将文字转换为音频流
    AzureEngine,        # 微软Azure的语音引擎
    ElevenlabsEngine,   # Elevenlabs的语音引擎
    SystemEngine,       # 系统自带的语音引擎
    CoquiEngine,        # Coqui的语音引擎
    OpenAIEngine,       # OpenAI的语音引擎
    KokoroEngine        # Kokoro的语音引擎
)

# 导入FastAPI相关的工具，用于创建网络服务
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.staticfiles import StaticFiles

# 导入其他需要的库
from queue import Queue  # 队列，用于存储音频数据
import threading         # 线程，让程序能同时做多件事
import logging           # 日志记录
import uvicorn           # ASGI服务器，运行FastAPI应用
import wave              # 处理WAV音频文件
import io                # 输入输出流处理

# 支持的语音引擎列表
SUPPORTED_ENGINES = [
    "azure",      # 微软Azure的语音服务
    "openai",     # OpenAI的语音服务
    "elevenlabs", # Elevenlabs的语音服务
    "system",     # 电脑系统自带的语音服务
    "coqui",      # 开源的Coqui语音服务（注释：如果需要频繁启动服务器进行测试，可以注释掉这一行）
    "kokoro"      # Kokoro语音服务
]

# 设置启动时使用的默认引擎
START_ENGINE = SUPPORTED_ENGINES[0]  # 使用列表中的第一个引擎（azure）作为默认引擎

# 浏览器标识符列表，用于检测请求是否来自浏览器
BROWSER_IDENTIFIERS = [
    "mozilla",  # Mozilla浏览器
    "chrome",   # Chrome浏览器
    "safari",   # Safari浏览器
    "firefox",  # Firefox浏览器
    "edge",     # Edge浏览器
    "opera",    # Opera浏览器
    "msie",     # 旧版Internet Explorer
    "trident",  # IE内核
]

# 允许跨域请求的源列表（域名）
# 跨域请求：当网页从一个域名的服务器请求另一个域名的服务器上的数据时
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

# 全局变量，存储程序运行所需的各种数据
audio_queue = Queue()  # 音频数据队列
play_text_to_speech_semaphore = threading.Semaphore(1)  # 信号量，控制同时只能有一个语音合成任务
engines = {}           # 存储初始化后的语音引擎
voices = {}            # 存储每个引擎支持的声音
current_engine = None  # 当前使用的引擎
stream = None          # 音频流对象
current_speaking = {}  # 记录当前正在播放的文本
speaking_lock = threading.Lock()  # 线程锁，防止多线程同时修改current_speaking
tts_lock = threading.Lock()       # 线程锁，用于文本转语音过程
gen_lock = threading.Lock()       # 线程锁，用于音频生成过程

# 创建FastAPI应用
app = FastAPI()
# 挂载静态文件目录，用于提供HTML、JS、CSS等静态资源
app.mount("/static", StaticFiles(directory="static"), name="static")
# 添加CORS中间件，允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 定义内容安全策略(CSP)，防止一些常见的网络攻击
csp = {
    "default-src": "'self'",      # 默认只允许加载自己域名下的资源
    "script-src": "'self'",       # 只允许加载自己域名下的脚本
    "style-src": "'self' 'unsafe-inline'",  # 允许内联样式
    "img-src": "'self' data:",    # 允许加载自己域名和data URL的图片
    "font-src": "'self' data:",   # 允许加载自己域名和data URL的字体
    "media-src": "'self' blob:",  # 允许加载自己域名和blob URL的媒体
}
csp_string = "; ".join(f"{key} {value}" for key, value in csp.items())


# HTTP中间件，为所有响应添加安全头部
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    '''
    这个函数会在每个HTTP请求处理前后运行
    它给所有HTTP响应添加了内容安全策略头部，增强网页安全性
    '''
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = csp_string
    return response


@app.get("/favicon.ico")
async def favicon():
    '''
    提供网站图标的接口
    当浏览器请求网站图标时，返回static文件夹中的favicon.ico文件
    网站图标就是浏览器标签页上显示的小图标
    '''
    return FileResponse("static/favicon.ico")


def _set_engine(engine_name):
    '''
    设置当前使用的语音引擎
    
    参数:
    engine_name - 要设置的引擎名称，必须是支持的引擎之一
    
    功能:
    1. 如果这是第一次设置引擎，创建一个新的TextToAudioStream对象
    2. 如果已经有引擎在运行，则切换到新的引擎
    3. 设置该引擎的默认声音（如果有可用声音）
    '''
    global current_engine, stream
    if current_engine is None:
        # 第一次设置引擎
        current_engine = engines[engine_name]
        stream = TextToAudioStream(current_engine, muted=True)
    else:
        # 切换到新引擎
        current_engine = engines[engine_name]
        stream.load_engine(current_engine)

    # 如果该引擎有可用的声音，设置第一个声音为默认声音
    if voices[engine_name]:
        engines[engine_name].set_voice(voices[engine_name][0].name)


@app.get("/set_engine")
def set_engine(request: Request, engine_name: str = Query(...)):
    '''
    网络接口：切换语音引擎
    
    参数:
    request - HTTP请求对象
    engine_name - 要切换到的引擎名称
    
    返回:
    成功: {"message": "切换到xxx引擎"}
    失败: {"error": "引擎不支持"} 或 {"error": "切换引擎失败"}
    
    例如: 访问 http://localhost:8000/set_engine?engine_name=azure 
    会切换到Azure语音引擎
    '''
    if engine_name not in engines:
        return {"error": "引擎不支持"}

    try:
        _set_engine(engine_name)
        return {"message": f"切换到 {engine_name} 引擎"}
    except Exception as e:
        logging.error(f"切换引擎时出错: {str(e)}")
        return {"error": "切换引擎失败"}


def play_text_to_speech(stream, text, audio_queue):
    '''
    将文字转换为语音并放入队列
    
    参数:
    stream - 音频流对象
    text - 要转换的文字
    audio_queue - 存放生成的音频数据的队列
    
    功能:
    1. 标记该文本正在进行语音合成
    2. 将文本输入到语音引擎
    3. 生成音频并将音频数据放入队列
    4. 完成后发送结束信号并释放信号量
    
    这就像把文字交给朗读员，朗读员一段一段地读出声音，
    每读一小段就把声音录下来放到队列里，最后表示读完了。
    '''
    set_speaking(text, True)

    def on_audio_chunk(chunk):
        '''当有音频数据生成时，把它放入队列'''
        logging.debug("收到音频数据块")
        audio_queue.put(chunk)

    try:
        stream.feed(text)  # 将文本输入到语音引擎
        logging.debug(f"为文本播放音频: {text}")
        print(f'正在合成: "{text}"')
        # 播放音频(实际上是生成音频并通过回调函数发送)
        stream.play(on_audio_chunk=on_audio_chunk, muted=True)
        audio_queue.put(None)  # 发送None表示音频流结束
    finally:
        # 无论成功还是失败，都执行以下操作
        set_speaking(text, False)
        play_text_to_speech_semaphore.release()


def is_browser_request(request):
    '''
    检查请求是否来自浏览器
    
    参数:
    request - HTTP请求对象
    
    返回:
    True - 如果请求来自浏览器
    False - 如果请求不是来自浏览器
    
    浏览器会在请求头中包含"user-agent"信息，
    通过检查这个信息中是否包含常见浏览器的标识符，
    我们可以判断请求是否来自浏览器。
    这就像看邮件的署名来判断是谁发的邮件一样。
    '''
    user_agent = request.headers.get("user-agent", "").lower()
    is_browser = any(browser_id in user_agent for browser_id in BROWSER_IDENTIFIERS)
    return is_browser


def create_wave_header_for_engine(engine):
    '''
    为音频引擎创建WAV文件头
    
    参数:
    engine - 当前使用的语音引擎
    
    返回:
    包含WAV文件头的字节数据
    
    WAV文件需要一个特定格式的文件头，其中包含
    音频的通道数、采样宽度、采样率等信息。
    这就像一本书的目录，告诉播放器如何正确解读后面的音频数据。
    '''
    _, _, sample_rate = engine.get_stream_info()

    num_channels = 1         # 单声道
    sample_width = 2         # 每个样本2个字节
    frame_rate = sample_rate # 采样率

    # 创建WAV文件头
    wav_header = io.BytesIO()
    with wave.open(wav_header, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(frame_rate)

    wav_header.seek(0)
    wave_header_bytes = wav_header.read()
    wav_header.close()

    # 创建一个新的BytesIO对象，为Firefox设置正确的MIME类型
    final_wave_header = io.BytesIO()
    final_wave_header.write(wave_header_bytes)
    final_wave_header.seek(0)

    return final_wave_header.getvalue()

def audio_chunk_generator(audio_queue, send_wave_headers):
    with gen_lock:
        first_chunk = False
        try:
            while True:
                chunk = audio_queue.get()
                if chunk is None:
                    logging.debug("Terminating stream")
                    break
                if not first_chunk:
                    if (
                        send_wave_headers
                        and not current_engine.engine_name == "elevenlabs"
                    ):
                        logging.debug("Sending wave header")
                        yield create_wave_header_for_engine(current_engine)
                    first_chunk = True
                logging.debug("Sending chunk")
                yield chunk
        except Exception as e:
            logging.error(f"Error during streaming: {str(e)}")


def is_currently_speaking(text):
    with speaking_lock:
        return current_speaking.get(text, False)


def set_speaking(text, status):
    with speaking_lock:
        current_speaking[text] = status


@app.get("/tts")
def tts(request: Request, text: str = Query(...)):
    browser_request = is_browser_request(request)
    audio_queue = Queue()

    if play_text_to_speech_semaphore.acquire(blocking=False):
        threading.Thread(
            target=play_text_to_speech, args=(stream, text, audio_queue), daemon=True
        ).start()
    else:
        raise HTTPException(
            status_code=503,
            detail="Service unavailable, currently processing another request. Please try again shortly.",
            headers={"Retry-After": "10"},
        )

    return StreamingResponse(
        audio_chunk_generator(audio_queue, browser_request),
        media_type="audio/wav"
        if current_engine.engine_name != "elevenlabs"
        else "audio/mpeg",
    )


@app.get("/tts-text")
def tts_text(request: Request, text: str = Query(...)):
    if "favicon.ico" in request.url.path:
        print("favicon requested")
        return FileResponse("static/favicon.ico")

    print(f"/tts_text route synthesizing text: {text}")

    browser_request = is_browser_request(request)

    if play_text_to_speech_semaphore.acquire(blocking=False):
        threading.Thread(
            target=play_text_to_speech, args=(stream, text), daemon=True
        ).start()
    else:
        logging.debug("Can't play audio, another instance is already running")

    return StreamingResponse(
        audio_chunk_generator(browser_request), media_type="audio/wav"
    )


@app.get("/engines")
def get_engines():
    return list(engines.keys())


@app.get("/voices")
def get_voices():
    voices_list = []
    for voice in voices[current_engine.engine_name]:
        voices_list.append(voice.name)
    return voices_list


@app.get("/setvoice")
def set_voice(request: Request, voice_name: str = Query(...)):
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
