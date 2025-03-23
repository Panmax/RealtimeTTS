"""
文本转语音(TTS)客户端程序 - 将文字转换成声音并实时播放

这个程序是做什么的？
这是一个客户端程序，它可以：
1. 把你输入的文字发送到服务器
2. 接收服务器返回的语音数据
3. 实时播放语音，就像有人在读这段文字一样
4. 可以选择将语音保存为音频文件

想象一下：你写了一段话，这个程序可以把它读出来，就像是有人在朗读你写的内容！
"""

import requests  # 用于发送网络请求，就像寄信一样
import pyaudio   # 用于播放声音，就像是扬声器一样
import time      # 用于计时，就像秒表一样
import wave      # 用于保存音频文件，就像录音机一样
import threading # 用于同时做多件事情，就像你一边听音乐一边写作业
import argparse  # 用于处理命令行参数，就像接收指令一样
from queue import Queue  # 用于存储音频数据，就像排队一样

# 参数解析器设置 - 让用户可以自定义程序的运行方式
"""
下面的代码是做什么的？
这就像是你在玩游戏前设置游戏选项：
- 可以选择服务器端口（就像选择和朋友通话的频道）
- 可以输入要转换的文字（就像告诉机器人你想让它说什么）
- 可以选择是否保存语音文件（就像决定是否录下这段对话）
"""
parser = argparse.ArgumentParser(description="运行TTS客户端。")
parser.add_argument("-p", "--port", type=int, default=8000, help="TTS服务器的端口 (默认: 8000)")
parser.add_argument(
    "-t",
    "--text",
    type=str,
    default="Hello! This is a default text to speech demo text!",
    help="要转换为语音的文本 (默认: 'Hello! This is a default text-to-speech demonstration...')"
)
parser.add_argument(
    "-w",
    "--write",
    action="store_true",
    help="将输出保存为WAV文件"
)
args = parser.parse_args()

port = args.port  # 从参数中获取端口号
text_to_tts = args.text  # 从参数中获取要转换的文本
write_to_file = args.write  # 是否保存为文件

# 配置信息 - 设置如何连接服务器和处理音频
"""
这部分就像是设置你的耳机和麦克风：
- 服务器地址告诉程序去哪里获取语音
- 音频格式设置告诉程序如何正确播放声音
"""
SERVER_URL = f"http://127.0.0.1:{port}/tts"  # 服务器地址，127.0.0.1表示本机
AUDIO_FORMAT = pyaudio.paInt16  # 音频格式，就像选择MP3还是WAV
CHANNELS = 1  # 单声道，只有一个声音通道，像单个扬声器
RATE = 16000  # 采样率，每秒钟采集的声音样本数，越高越清晰

# 初始化音频播放器 - 准备好播放声音
"""
这就像是打开你的音箱，准备好接收声音信号并播放出来
"""
pyaudio_instance = pyaudio.PyAudio()  # 创建音频处理对象
stream = pyaudio_instance.open(
    format=AUDIO_FORMAT, channels=CHANNELS, rate=RATE, output=True
)  # 打开音频流，就像打开音箱的开关


# 如果需要保存音频，就准备好WAV文件
if write_to_file:
    output_wav_file = 'output_audio.wav'  # 输出文件名
    wav_file = wave.open(output_wav_file, 'wb')  # 打开文件准备写入
    wav_file.setnchannels(CHANNELS)  # 设置声道数
    wav_file.setsampwidth(pyaudio_instance.get_sample_size(AUDIO_FORMAT))  # 设置采样宽度
    wav_file.setframerate(RATE)  # 设置采样率

# 音频数据队列 - 存储等待播放的音频片段
"""
这就像是一个音乐播放列表，音频片段一个接一个地排队等待播放
"""
chunk_queue = Queue()

# 播放音频的线程函数 - 负责从队列中取出音频片段并播放
"""
这个函数就像是一个DJ，负责按顺序播放所有的音乐片段：
1. 首先收集足够的音频片段，避免播放中断
2. 然后开始播放，并继续收集更多片段
3. 直到所有片段都播放完毕
"""
def play_audio():
    global start_time  # 使用全局变量来记录开始时间
    buffer = b""  # 音频缓冲区，存储待播放的音频数据
    played_out = False  # 是否已经开始播放的标志
    got_first_chunk = False  # 是否收到第一个音频片段的标志

    # 计算每一帧音频的大小
    frame_size = pyaudio_instance.get_sample_size(AUDIO_FORMAT) * CHANNELS
    min_buffer_size = 1024 * 6   # 最小缓冲区大小，就像先攒一些水再倒出来

    # 初始缓冲 - 先收集足够的音频数据，避免播放断断续续
    while len(buffer) < min_buffer_size:
        chunk = chunk_queue.get()  # 从队列获取一个音频片段
        if chunk is None:  # 如果收到结束信号
            break
        if not got_first_chunk:  # 如果这是第一个音频片段
            got_first_chunk = True
            time_to_first_token = time.time() - start_time
            print(f"收到第一个音频片段的时间: {time_to_first_token}秒")
        buffer += chunk  # 将片段添加到缓冲区

    # 开始播放音频
    while True:
        # 如果缓冲区有足够的数据，就播放一部分
        if len(buffer) >= frame_size:
            num_frames = len(buffer) // frame_size  # 计算可以播放多少完整帧
            bytes_to_write = num_frames * frame_size  # 计算要播放的字节数
            if not played_out:  # 如果这是第一次播放
                played_out = True
                time_to_first_token = time.time() - start_time
                # print(f"首次播放的时间: {time_to_first_token}秒")
            stream.write(buffer[:bytes_to_write])  # 播放音频数据
            buffer = buffer[bytes_to_write:]  # 移除已播放的部分
        else:
            # 获取更多数据
            chunk = chunk_queue.get()
            if chunk is None:  # 如果收到结束信号
                # 播放剩余的数据
                if len(buffer) > 0:
                    # 裁剪缓冲区，确保长度是帧大小的整数倍
                    if len(buffer) % frame_size != 0:
                        buffer = buffer[:-(len(buffer) % frame_size)]

                    if not played_out:
                        played_out = True
                        time_to_first_token = time.time() - start_time
                        # print(f"首次播放的时间: {time_to_first_token}秒")
                    stream.write(buffer)  # 播放最后的音频数据
                break  # 退出循环
            buffer += chunk  # 将新片段添加到缓冲区


# 请求文本转语音并接收音频数据的函数
"""
这个函数就像是给朋友发信息并等待回复：
1. 发送文本到服务器，请求将文字转成语音
2. 等待服务器返回音频数据
3. 将收到的每一块音频数据放入队列等待播放
"""
def request_tts(text):
    global start_time
    start_time = time.time()  # 记录开始时间
    try:
        # 发送请求到服务器，请求将文本转换为语音
        response = requests.get(SERVER_URL, params={"text": text}, stream=True, timeout=10)
        response.raise_for_status()  # 检查请求是否成功，如果失败会抛出异常

        # 随着数据的到达处理音频块
        for chunk in response.iter_content(chunk_size=None):
            if chunk:  # 如果收到有效的数据
                if write_to_file:  # 如果需要保存到文件
                    wav_file.writeframes(chunk)  # 写入WAV文件
                chunk_queue.put(chunk)  # 放入队列等待播放
        
        # 发送结束信号
        chunk_queue.put(None)

    except requests.exceptions.RequestException as e:
        print(f"发生错误: {e}")
        chunk_queue.put(None)  # 确保播放线程能够正常退出

# 主程序流程
"""
这就像是一出戏剧的主线：
1. 先启动一个专门负责播放声音的助手（线程）
2. 然后自己负责向服务器请求语音数据
3. 无论发生什么，最后都要确保清理干净（关闭所有设备）
"""
# 启动音频播放线程
playback_thread = threading.Thread(target=play_audio)
playback_thread.start()

# 在主线程中请求并接收音频数据
try:
    request_tts(text_to_tts)  # 发送文本请求转换为语音
finally:
    # 无论如何都要清理资源
    playback_thread.join()  # 等待播放线程结束
    stream.stop_stream()  # 停止音频流
    stream.close()  # 关闭音频流
    pyaudio_instance.terminate()  # 终止音频实例
    if write_to_file:  # 如果打开了文件
        wav_file.close()  # 关闭WAV文件

"""
整个程序的工作方式总结：

想象你在用对讲机和朋友交流：
1. 你说了一句话（文本）
2. 对讲机把你的话发送出去（发送请求）
3. 朋友听到后开始回答（服务器处理）
4. 你一边听到朋友回答的声音（接收音频），一边能听到它（播放声音）

这个程序的特别之处是"实时"性质：
- 不需要等待所有语音都生成好才开始播放
- 而是收到一部分就立即播放一部分，就像真人对话一样
- 这样可以让用户感觉响应更快，体验更好

如果你想看到声音，可以用参数"-w"将音频保存为文件，
就像录下对话一样，之后还可以再听！
"""
