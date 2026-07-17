import json
import urllib.request
import urllib.error
import asyncio
import threading
import queue
from typing import Optional

class HTTPStatusError(Exception):
    def __init__(self, response):
        self.response = response
        super().__init__(f"HTTP Error {response.status_code}")

class HTTPError(Exception):
    pass

class Response:
    def __init__(self, status_code: int, headers: dict, text: str, stream_queue: Optional[queue.Queue] = None):
        self.status_code = status_code
        self.headers = headers
        self.text = text
        self._queue = stream_queue

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPStatusError(self)

    def json(self):
        return json.loads(self.text)

    async def aiter_lines(self):
        if not self._queue:
            return
        while True:
            try:
                item_type, data = self._queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if item_type == "error":
                raise HTTPError(str(data))
            elif item_type == "done":
                break
            elif item_type == "line":
                yield data

class AsyncStreamContextManager:
    def __init__(self, coro):
        self.coro = coro
        self.resp = None
    async def __aenter__(self):
        self.resp = await self.coro
        return self.resp
    async def __aexit__(self, exc_type, exc, tb):
        pass

class AsyncClient:
    def __init__(self, timeout=120):
        self.timeout = timeout

    async def aclose(self):
        pass

    async def post(self, url, json=None, headers=None, timeout=None, stream=False):
        if stream:
            return AsyncStreamContextManager(self._do_post(url, json, headers, timeout or self.timeout, stream=True))
        return await self._do_post(url, json, headers, timeout or self.timeout, stream=False)

    async def _do_post(self, url, payload, headers, timeout, stream):
        data = json.dumps(payload).encode('utf-8') if payload else None
        req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")

        if not stream:
            def _sync():
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        body = resp.read().decode('utf-8')
                        return Response(resp.status, dict(resp.headers), body)
                except urllib.error.HTTPError as e:
                    body = e.read().decode('utf-8')
                    return Response(e.code, dict(e.headers), body)
                except Exception as e:
                    raise HTTPError(str(e))
            return await asyncio.to_thread(_sync)

        else:
            q = queue.Queue(maxsize=500)
            status_q = queue.Queue(maxsize=1)
            
            def _reader():
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        status_q.put((resp.status, dict(resp.headers), None))
                        for line in resp:
                            q.put(("line", line.decode('utf-8')))
                        q.put(("done", None))
                except urllib.error.HTTPError as e:
                    body = e.read().decode('utf-8')
                    status_q.put((e.code, dict(e.headers), body))
                except Exception as e:
                    status_q.put((0, {}, str(e)))

            t = threading.Thread(target=_reader, daemon=True)
            t.start()

            # Wait for headers to arrive on the queue
            while True:
                try:
                    status, headers_dict, body_or_err = status_q.get_nowait()
                    break
                except queue.Empty:
                    await asyncio.sleep(0.01)
            
            if status == 0:
                raise HTTPError(body_or_err)
                
            return Response(status, headers_dict, body_or_err or "", stream_queue=q)
