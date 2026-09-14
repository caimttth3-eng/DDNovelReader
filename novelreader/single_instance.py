# -*- coding: utf-8 -*-
"""跨平台单实例（Windows / macOS / Linux 通用）。

原理：主实例在本地回环地址 127.0.0.1:PORT 上监听一个 TCP 端口；
第二个实例 bind 失败后连接该端口并发送 MAGIC，主实例收到后回 ACK
并把“激活窗口”请求放入队列，由主线程轮询还原已有窗口；
第二个实例随即退出，不会出现两个主程序。

误判防护：端口被非本软件占用时，第二个实例握手拿不到 ACK，同样退出
（保证数据唯一性），并向 stderr 打印原因。
"""
import socket
import threading
import time

PORT = 47652
MAGIC = b"DDNR-ACTIVATE-V1"
ACK = b"DDNR-ACK"


class SingleInstance:
    def __init__(self, port=PORT):
        self.port = port
        self._srv = None
        self._queue = None
        self.acquired = self._try_bind()

    def _try_bind(self, attempts=4):
        # 注意：不能设置 SO_REUSEADDR——Windows 上它允许两个 socket 重复绑定
        # 同一端口，会破坏单实例；监听 socket 无活跃连接时重启可立即重绑，
        # 若遇 TIME_WAIT 短占用则小睡重试。
        for _ in range(attempts):
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", self.port))
                s.listen(2)
                self._srv = s
                return True
            except OSError:
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
                time.sleep(0.25)
        return False

    def notify_existing(self):
        """第二个实例：连接主实例发送激活请求。返回是否拿到确认。"""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as c:
                c.sendall(MAGIC)
                c.settimeout(2)
                if c.recv(16) == ACK:
                    return True
        except OSError:
            pass
        return False

    def start_listener(self, queue_):
        """主实例：后台线程 accept；收到 MAGIC 后回 ACK 并放入队列。"""
        self._queue = queue_
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            try:
                with conn:
                    conn.settimeout(2)
                    data = conn.recv(64)
                    if MAGIC in data:
                        try:
                            conn.sendall(ACK)
                        except OSError:
                            pass
                        if self._queue is not None:
                            self._queue.put("activate")
            except OSError:
                continue

    def close(self):
        try:
            if self._srv:
                self._srv.close()
        except OSError:
            pass
