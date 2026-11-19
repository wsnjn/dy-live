# -*- coding: utf-8 -*-
"""直播间主播声音 -> 文字（实时语音转写，FunASR / Paraformer）。

用 PyAV 拉直播流 AAC 音频，按固定时长切片后交给阿里 Paraformer 识别（中文更强）。
模型首次使用会从 ModelScope 下载（国内快，无需翻墙）。
"""
import io
import os
import sys
import threading
import wave

# 支持直接运行 `python dy_live/transcribe.py`：把项目根目录加入 sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import av

from dy_apis.douyin_api import DouyinAPI
from utils.fingerprint import get_profile

# 低清优先：只取音频，没必要拉高清视频
_QUALITY_ORDER = ("SD2", "SD1", "HD1", "FULL_HD1")


def resolve_stream_url(auth, web_rid):
    """从 room/web/enter 里挑一个可用的拉流地址。"""
    resp = DouyinAPI.get_live_room_enter(auth, web_rid)
    rooms = (resp.get("data") or {}).get("data") or []
    if not rooms:
        raise RuntimeError(f"直播间 {web_rid} 未开播或不存在")
    stream_url = rooms[0].get("stream_url") or {}

    flv = stream_url.get("flv_pull_url") or {}
    if isinstance(flv, dict):
        for q in _QUALITY_ORDER:
            url = flv.get(q)
            if isinstance(url, str) and url.startswith("http"):
                return url

    hls = stream_url.get("hls_pull_url_map") or {}
    if isinstance(hls, dict):
        for v in hls.values():
            if isinstance(v, str) and v.startswith("http"):
                return v

    rtmp = stream_url.get("rtmp_pull_url")
    if isinstance(rtmp, str) and rtmp.startswith(("rtmp", "http")):
        return rtmp

    raise RuntimeError(f"直播间 {web_rid} 未返回可用的拉流地址（可能已下播）")


class LiveTranscriber:
    SAMPLE_RATE = 16000

    def __init__(self, auth, web_rid, on_text=None, on_status=None, model_size=None,
                 chunk_sec=8.0, language="zh", device="cpu", compute_type="int8"):
        # model_size / language / compute_type 仅为兼容旧调用保留，FunASR 不使用
        self.auth = auth
        self.web_rid = web_rid
        self.on_text = on_text
        self.on_status = on_status
        self.chunk_sec = chunk_sec
        self.device = device
        self._stopping = False
        self._model = None

    def _report(self, msg):
        if self.on_status:
            self.on_status(msg)

    # ---------- 模型 ----------
    def load_model(self):
        if self._model is not None:
            return self._model
        from funasr import AutoModel
        self._report("正在下载/加载 FunASR 模型（首次需下载，约几分钟，请耐心等待）...")
        self._model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=self.device,
            disable_update=True,
        )
        self._report("FunASR 模型加载完成")
        return self._model

    def stop(self):
        self._stopping = True

    # ---------- 主流程 ----------
    def run(self):
        self.load_model()
        self._report("正在连接直播音频流...")
        url = resolve_stream_url(self.auth, self.web_rid)
        self._report("音频流已连接，开始实时转写...")
        ua = (get_profile() or {}).get("ua") or "Mozilla/5.0"
        headers = (
            f"Referer: https://live.douyin.com/\r\n"
            f"User-Agent: {ua}\r\n"
        )
        container = av.open(url, options={
            "fflags": "nobuffer",
            "timeout": "10000000",
            "headers": headers,
        })
        resampler = av.AudioResampler(format="s16", layout="mono",
                                      rate=self.SAMPLE_RATE)
        chunk_bytes = int(self.SAMPLE_RATE * 2 * self.chunk_sec)
        buf = bytearray()
        try:
            for frame in container.decode(audio=0):
                if self._stopping:
                    break
                for rf in resampler.resample(frame):
                    buf += rf.to_ndarray().tobytes()
                while len(buf) >= chunk_bytes and not self._stopping:
                    pcm = bytes(buf[:chunk_bytes])
                    del buf[:chunk_bytes]
                    self._transcribe(pcm)
        finally:
            container.close()
        if buf and not self._stopping:
            self._transcribe(bytes(buf))

    # ---------- 识别 ----------
    def _transcribe(self, pcm_bytes):
        # 把 PCM 转成 WAV bytes 喂给 FunASR
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(pcm_bytes)
        try:
            res = self._model.generate(input=wav_buf.getvalue())
            text = (res[0].get("text") or "").strip() if res else ""
        except Exception:
            return
        if text and self.on_text:
            self.on_text(text)


def transcribe_in_thread(transcriber):
    """在后台线程里运行转写，供 GUI / 服务复用。"""
    t = threading.Thread(target=transcriber.run, daemon=True)
    t.start()
    return t


def ensure_model():
    """预下载并缓存 FunASR 模型，之后运行时加载更快。

    可在启动 GUI 前单独跑一次：
        .venv\\Scripts\\python dy_live\\transcribe.py
    """
    t = LiveTranscriber(None, "")
    t.load_model()
    print("FunASR 模型已就绪")


if __name__ == "__main__":
    ensure_model()

