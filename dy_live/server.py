import gzip
import sys
import threading
import time
from urllib.parse import urlencode

from websocket import WebSocketApp

import static.Live_pb2 as Live_pb2
from dy_live.pk import PK_MESSAGES, PKMessageHandler, print_pk_event
from dy_apis.douyin_api import DouyinAPI
from builder.header import HeaderBuilder
from builder.params import Params
import utils.common_util as common_util
from utils.dy_util import generate_signature

# Windows 控制台是 GBK，弹幕含 emoji 会 UnicodeEncodeError，按 UTF-8 输出
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _user_info(user):
    """把 protobuf User 转成字典，供 GUI 结构化渲染（含粉丝团/等级/关注状态）。"""
    badges = []
    for img in getattr(user, "badge_image_list", []) or []:
        c = getattr(img, "content", None)
        if c is None:
            continue
        name = (getattr(c, "name", "") or "").strip()
        alt = (getattr(c, "alternative_text", "") or "").strip()
        level = getattr(c, "level", 0) or 0
        if name or alt or level:
            badges.append({"name": name, "alt": alt, "level": level})
    return {
        # 真实数据里 sec_uid 在字段 73（proto 中名为 webcast_uid，值为 MS4wLj... 格式），
        # 字段 46 的 sec_uid 在新版 webcast 里已为空，这里优先取 73。
        "sec_uid": (getattr(user, "webcast_uid", "") or getattr(user, "sec_uid", "") or "").strip(),
        "nickname": (getattr(user, "nickname", "") or "").strip(),
        "display_id": (getattr(user, "display_id", "") or "").strip(),
        "level": getattr(user, "level", 0) or 0,
        "gender": getattr(user, "gender", 0) or 0,
        "city": (getattr(user, "city", "") or "").strip(),
        "is_follower": bool(getattr(user, "is_follower", False)),
        "is_following": bool(getattr(user, "is_following", False)),
        "follow_status": getattr(user, "follow_status", 0) or 0,
        "fan_ticket_count": getattr(user, "fan_ticket_count", 0) or 0,
        # 粉丝画像扩展字段（匿名/神秘人、付费、近30天观看等）
        "mystery_man": getattr(user, "mystery_man", 0) or 0,
        "is_anonymous": bool(getattr(user, "is_anonymous", False)),
        "desensitized_nickname": (getattr(user, "desensitized_nickname", "") or "").strip(),
        "pay_score": getattr(user, "pay_score", 0) or 0,
        "pay_scores": getattr(user, "pay_scores", 0) or 0,
        "total_recharge_diamond_count": getattr(user, "total_recharge_diamond_count", 0) or 0,
        "consume_diamond_level": getattr(user, "consume_diamond_level", 0) or 0,
        "watch_duration_month": getattr(user, "watch_duration_month", 0) or 0,
        "top_vip_no": getattr(user, "top_vip_no", 0) or 0,
        "user_role": getattr(user, "user_role", 0) or 0,
        "special_id": (getattr(user, "special_id", "") or "").strip(),
        "verified": bool(getattr(user, "verified", False)),
        "badges": badges,
    }


# 抖音 webcast GiftStruct.diamond_count（礼物价值/钻石数）字段号。
# 本项目裁剪后的 Live.proto 未包含该字段，protobuf 解析时会把它保留在消息的
# unknown fields 中；下面把 GiftStruct 序列化回字节流，再手解析字段 6 读出。
_GIFT_DIAMOND_COUNT_FIELD = 6


def _read_varint_field(data, field_number):
    """从 protobuf 字节流里读指定字段号的首个 varint 值（纯 Python 手解析，兼容各种后端）。"""
    i, n = 0, len(data)
    while i < n:
        key = 0
        shift = 0
        while i < n:
            b = data[i]
            i += 1
            key |= (b & 0x7f) << shift
            if not (b & 0x80):
                break
            shift += 7
        fn, wt = key >> 3, key & 0x7
        if wt == 0:  # varint
            val, shift = 0, 0
            while i < n:
                b = data[i]
                i += 1
                val |= (b & 0x7f) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if fn == field_number:
                return val
        elif wt == 2:  # length-delimited
            ln, shift = 0, 0
            while i < n:
                b = data[i]
                i += 1
                ln |= (b & 0x7f) << shift
                if not (b & 0x80):
                    break
                shift += 7
            i += ln
        elif wt == 5:  # 32-bit fixed
            i += 4
        elif wt == 1:  # 64-bit fixed
            i += 8
        else:  # 未知 wire type，终止解析
            break
    return 0


def _gift_diamond_count(gift_struct):
    """取礼物价值（GiftStruct.diamond_count，字段号 6）。"""
    if gift_struct is None:
        return 0
    try:
        return _read_varint_field(gift_struct.SerializeToString(), _GIFT_DIAMOND_COUNT_FIELD)
    except Exception:
        return 0


class DouyinLive:
    def __init__(self, live_id, auth_, on_pk_event=None, on_live_event=None):
        self.auth_ = auth_
        self.live_id = live_id
        self.ws = None
        # Optional structured PK consumer; normal CLI printing stays enabled.
        self.on_pk_event = on_pk_event
        # Optional structured chat/gift/member/like/follow/room consumer (GUI).
        self.on_live_event = on_live_event
        self.pk_handler = PKMessageHandler()

    def ping(self, ws):
        while True:
            frame = Live_pb2.PushFrame()
            frame.payloadType = "hb"
            try:
                ws.send(frame.SerializeToString(), opcode=0x02)
                time.sleep(5)
            except Exception as e:
                ws.close()
                break

    def on_open(self, ws):
        print("\033[32m### opened ###\033[m")
        # A reconnect may have missed an entire battle or a channel change.
        self.pk_handler = PKMessageHandler()
        if hasattr(self.auth_, "live_websocket_connected"):
            self.auth_.live_websocket_connected = True
        threading.Thread(target=self.ping, args=(ws,)).start()

    def on_message(self, ws, message):
        try:
            frame = Live_pb2.PushFrame()
            frame.ParseFromString(message)
            if frame.payloadType in ('hb', 'ack') or not frame.payload:
                return
            origin_bytes = frame.payload
            if origin_bytes.startswith(b'\x1f\x8b'):
                origin_bytes = gzip.decompress(origin_bytes)
            response = Live_pb2.LiveResponse()
            response.ParseFromString(origin_bytes)
            if hasattr(self.auth_, "live_websocket_verified"):
                # A successful protobuf parse proves the websocket payload
                # path, whereas on_open alone only proves the handshake.
                self.auth_.live_websocket_verified = True
            if response.needAck:
                s = Live_pb2.PushFrame()
                s.payloadType = "ack"
                # s.payload = frame.headersList[1].value.encode('utf-8')
                s.payload = response.internalExt.encode('utf-8')
                s.logId = frame.logId
                ws.send(s.SerializeToString(), opcode=0x02)
            for item in response.messagesList:
                if item.method.removeprefix('Webcast') in PK_MESSAGES:
                    try:
                        event = self.pk_handler.handle(item.method, item.payload, item.msgId)
                        if event is not None:
                            print_pk_event(event)
                            if self.on_pk_event is not None:
                                self.on_pk_event(event)
                    except Exception as exc:
                        # One malformed PK payload/callback must not swallow the
                        # other PK/chat/gift items in this response.
                        print(f'[PK 消息处理失败] method={item.method} msg_id={item.msgId}: {exc}')
                    continue
                elif item.method == 'WebcastGiftMessage':
                    message = Live_pb2.GiftMessage()
                    message.ParseFromString(item.payload)
                    if self.on_live_event is not None:
                        self.on_live_event({
                            "type": "gift",
                            "user": _user_info(message.user),
                            "to_user": _user_info(message.toUser),
                            "gift_name": getattr(message.gift, "name", "") or "",
                            "gift_count": getattr(message, "comboCount", 0) or 0,
                            "gift_id": (getattr(message, "giftId", "") or "").strip(),
                            "gift_struct_id": getattr(message.gift, "id", 0) or 0,
                            "diamond_count": _gift_diamond_count(message.gift),
                        })
                    else:
                        print(f'\033[1;37;40m[礼物]SEC_UID = {message.user.sec_uid} - {message.user.nickname}\033[m 送给 \033[1;37;40m{message.toUser.sec_uid} - {message.toUser.nickname}\033[m \033[4;30;44m{message.gift.name}\033[m x {message.comboCount}')
                elif item.method == "WebcastChatMessage":
                    message = Live_pb2.ChatMessage()
                    message.ParseFromString(item.payload)
                    if self.on_live_event is not None:
                        self.on_live_event({
                            "type": "chat",
                            "user": _user_info(message.user),
                            "content": getattr(message, "content", "") or "",
                        })
                    else:
                        print(f'\033[1;37;40m[消息]SEC_UID = {message.user.sec_uid} - {message.user.nickname}\033[m : \033[4;30;44m{message.content}\033[m')
                elif item.method == "WebcastMemberMessage":
                    message = Live_pb2.MemberMessage()
                    message.ParseFromString(item.payload)
                    if self.on_live_event is not None:
                        self.on_live_event({
                            "type": "member",
                            "user": _user_info(message.user),
                            "member_count": getattr(message, "memberCount", 0) or 0,
                        })
                    else:
                        print(f'\033[1;37;40m[进入]SEC_UID = {message.user.sec_uid} - {message.user.nickname}\033[m 进入直播间')
                elif item.method == "WebcastLikeMessage":
                    message = Live_pb2.LikeMessage()
                    message.ParseFromString(item.payload)
                    if self.on_live_event is not None:
                        self.on_live_event({
                            "type": "like",
                            "user": _user_info(message.user),
                            "count": getattr(message, "count", 0) or 0,
                            "total": getattr(message, "total", 0) or 0,
                        })
                    else:
                        print(f'\033[1;37;40m[点赞]SEC_UID = {message.user.sec_uid} - {message.user.nickname}\033[m 点赞了 {message.count} 次')
                        print(f'\033[1;37;40m[点赞]点赞总数 = {message.total}\033[m')
                elif item.method == "WebcastSocialMessage":
                    message = Live_pb2.SocialMessage()
                    message.ParseFromString(item.payload)
                    if message.action == 1:
                        if self.on_live_event is not None:
                            self.on_live_event({
                                "type": "follow",
                                "user": _user_info(message.user),
                            })
                        else:
                            print(f'\033[1;37;40m[关注]SEC_UID = {message.user.sec_uid} - {message.user.nickname}\033[m 关注主播')
                elif item.method == "WebcastRoomStatsMessage":
                    message = Live_pb2.RoomStatsMessage()
                    message.ParseFromString(item.payload)
                    if self.on_live_event is not None:
                        self.on_live_event({
                            "type": "room",
                            "display": getattr(message, "displayLong", "") or "",
                        })
                    else:
                        print(f'\033[1;37;40m[房间信息] {message.displayLong}')

            # s = zlib.decompress(decode_str).decode()
        except Exception as e:
            print('error')
            print(str(e))

    def on_error(self, ws, error):
        print("\033[31m### error ###")
        print(error)
        print("### ===error=== ###\033[m")

    def on_close(self, ws, close_status_code, close_msg):
        # 此处判断是否需要重连 判断直播间是否关闭
        self.start_ws()
        print("\033[31m### closed ###")
        print(f"status_code: {close_status_code}, msg: {close_msg}")
        print("### ===closed=== ###\033[m")

    def start_ws(self):
        room_info = DouyinAPI.get_live_info(self.auth_, self.live_id)
        room_id = room_info['room_id']
        user_id = room_info['user_id']
        ttwid = room_info['ttwid']
        params = Params()

        res = DouyinAPI.get_webcast_detail(self.auth_, str(user_id), room_id, f"https://live.douyin.com/{self.live_id}")
        frame = Live_pb2.LiveResponse()
        frame.ParseFromString(res)
        (params
         .add_param('app_name', 'douyin_web')
         .add_param('version_code', '180800')
         .add_param('webcast_sdk_version', '1.0.15')
         .add_param('update_version_code', '1.0.15')
         .add_param('compress', 'gzip')
         .add_param('device_platform', 'web')
         .add_param('cookie_enabled', 'true')
         .add_param('screen_width', '1707')
         .add_param('screen_height', '960')
         .add_param('browser_language', 'zh-CN')
         .add_param('browser_platform', 'Win32')
         .add_param('browser_name', 'Mozilla')
         .add_param('browser_version',
                    HeaderBuilder.ua.split('Mozilla/')[-1])
         .add_param('browser_online', 'true')
         .add_param('tz_name', 'Etc/GMT-8')
         .add_param('cursor', str(frame.cursor))
         .add_param('internal_ext', frame.internalExt)
         .add_param('host', 'https://live.douyin.com')
         .add_param('aid', '6383')
         .add_param('live_id', '1')
         .add_param('did_rule', '3')
         .add_param('endpoint', 'live_pc')
         .add_param('support_wrds', '1')
         .add_param('user_unique_id', str(user_id))
         .add_param('im_path', '/webcast/im/fetch/')
         .add_param('identity', 'audience')
         .add_param('need_persist_msg_count', '15')
         .add_param('insert_task_id', '')
         .add_param('live_reason', '')
         .add_param('room_id', room_id)
         .add_param('heartbeatDuration', '0')
         .add_param('signature', generate_signature(room_id, user_id))
         )
        wss_url = f"wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/?{urlencode(params.get())}"
        self.ws = WebSocketApp(
            url=wss_url,
            header={
                'Pragma': 'no-cache',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'User-Agent': HeaderBuilder.ua,
                'Upgrade': 'websocket',
                'Cache-Control': 'no-cache',
                'Connection': 'Upgrade',
            },
            cookie=self.auth_.cookie_str,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        try:
            self.ws.run_forever(origin='https://live.douyin.com')
        except Exception as e:
            print(str(e))
            self.ws.close()


if __name__ == '__main__':
    common_util.load_env()
    live_id = "75409558696"
    # Live REST/WebSocket uses the same Auth session as the main-site APIs;
    # an explicit DY_LIVE_COOKIES remains an opt-in legacy override handled by
    # load_env().
    live = DouyinLive(live_id, common_util.get_live_auth())
    live.start_ws()
