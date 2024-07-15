# encoding:utf-8

import requests
import plugins
import os
import json
from bridge.reply import Reply, ReplyType
from bridge.context import ContextType, Context  # 导入 Context 类
from plugins.event import Event, EventAction, EventContext
from channel.chat_message import ChatMessage
from common.log import logger
from bridge.bridge import Bridge  # 调整导入路径
from plugins import Plugin  # 导入 Plugin 类
import threading  # 导入 threading 模块

# 用于暂存用户文本消息的字典
user_text_cache = {}
user_file_cache = {}

@plugins.register(
    name="file_uploader",
    desire_priority=820,  # 确保优先级高于 sum4all
    desc="A plugin for uploading files to Coze and combining with text",
    version="0.1.1",
    author="xiaolong",
)
class file_uploader(Plugin):
    def __init__(self):
        super().__init__()
        try:
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                # 使用父类的方法来加载配置
                self.config = super().load_config()

                if not self.config:
                    raise Exception("config.json not found")
            
            # 设置事件处理函数
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

            # 从配置中提取所需的设置
            self.coze_key = self.config.get("coze_key", "")

            if not self.coze_key:
                raise ValueError("coze_key not found in config")

            logger.info("[file_uploader] inited with coze_key")

        except Exception as e:
            logger.error(f"file_uploader init failed: {e}")

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]
        msg: ChatMessage = e_context["context"]["msg"]  # 获取 ChatMessage 对象

        # 获取用户ID
        user_id = msg.from_user_id

        # 处理文字消息
        if context.type == ContextType.TEXT:
            # 缓存用户发送的文字消息
            user_text_cache[user_id] = msg.content
            logger.info(f"缓存用户 {user_id} 的文本消息: {msg.content}")
            # 检查是否有缓存的文件
            if user_id in user_file_cache:
                threading.Thread(target=self.process_combined_message, args=(user_id, context)).start()

        # 处理文件消息
        elif context.type in [ContextType.IMAGE, ContextType.FILE]:
            logger.info("on_handle_context: 开始处理文件")
            context.get("msg").prepare()
            file_path = context.content
            logger.info(f"on_handle_context: 获取到文件路径 {file_path}")

            file_id, file_name = self.upload_to_coze(file_path)
            if file_id:
                # 缓存用户的文件 ID 和文件名
                user_file_cache[user_id] = (file_id, file_name)
                logger.info(f"缓存用户 {user_id} 的文件 ID: {file_id}")

                # 立即返回文件 ID 给用户，并询问如何处理
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = f"文件上传成功，文件 ID: {file_id}\n文件名: {file_name}\n请问您需要对该文件进行什么操作？"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS

                # 检查是否有缓存的文字消息
                if user_id in user_text_cache:
                    threading.Thread(target=self.process_combined_message, args=(user_id, context)).start()
            else:
                # 文件上传失败
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "文件上传失败，请稍后再试"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS

    def process_combined_message(self, user_id, context):
        # 获取缓存的文本和文件
        text_message = user_text_cache.pop(user_id, "")
        file_id, file_name = user_file_cache.pop(user_id, "")

        # 整合消息并添加执行工作流的指令
        combined_message = f"文件 ID: {file_id}\n文件名: {file_name}\n文本内容: {text_message}"
        
        # 发送整合后的消息给 ByteDanceCozeBot
        self.send_to_coze_bot(combined_message, context)

    def upload_to_coze(self, file_path):
        url = 'https://api.coze.cn/v1/files/upload'
        files = {'file': open(file_path, 'rb')}
        headers = {'Authorization': f'Bearer {self.coze_key}'}
        logger.info(f"Uploading file with Coze Key: {self.coze_key}")
        try:
            response = requests.post(url, files=files, headers=headers)
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()
            if data['code'] == 0:
                file_id = data['data']['id']
                file_name = data['data']['file_name']
                logger.info(f"文件上传成功，文件 ID: {file_id}")
                return file_id, file_name
            else:
                logger.error(f"文件上传失败: {data['msg']}")
                return None, None
        except requests.exceptions.RequestException as e:
            logger.error(f"文件上传请求出错: {e}")
            return None, None

    def send_to_coze_bot(self, message, original_context):
        try:
            # 通过 Bridge 获取 ByteDanceCozeBot 实例并发送消息
            bridge = Bridge()
            coze_bot = bridge.get_bot("chat")  # 获取 ByteDanceCozeBot 实例

            # 从原始 context 复制必要的属性
            context = Context(type=ContextType.TEXT, content=message)
            context["session_id"] = original_context.get("session_id")
            context["msg"] = original_context.get("msg")
            context["user_id"] = original_context.get("user_id")

            reply = coze_bot.reply(message, context)  # 发送消息
            logger.info(f"已将消息发送到 ByteDanceCozeBot: {message}")
            return reply
        except Exception as e:
            logger.error(f"发送消息到 ByteDanceCozeBot 失败: {e}")
