"""Bounded business callbacks on the parent-owned UTF-8 pipes; no Nimi credentials."""
import asyncio
import concurrent.futures
import json
import sys
import threading
import uuid

_pending: dict[str, concurrent.futures.Future] = {}
_lock = threading.Lock()
_write_lock = threading.Lock()


def _send(value):
    encoded = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8", "strict")
    with _write_lock:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()


def start(on_closed):
    def read():
        try:
            for raw in sys.stdin.buffer:
                response = json.loads(raw.decode("utf-8", "strict"))
                with _lock:
                    future = _pending.get(response["id"])
                if future:
                    try:
                        if "error" in response:
                            future.set_exception(RuntimeError(response["error"]))
                        else:
                            future.set_result(response["result"])
                    except concurrent.futures.InvalidStateError:
                        pass  # The caller cancelled while this reply was in transit.
        finally:
            with _lock:
                for future in _pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("VoiceStudio Host disconnected"))
            on_closed()
    threading.Thread(target=read, name="business-callbacks", daemon=True).start()


async def call(operation, payload):
    if operation not in {"synthesize", "transcribe", "translate", "separate", "status", "recover-speech"}:
        raise ValueError("Unknown VoiceStudio operation")
    request_id = uuid.uuid4().hex
    future = concurrent.futures.Future()
    with _lock:
        _pending[request_id] = future
    try:
        _send({"id": request_id, "operation": operation, "input": payload})
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        _send({"id": request_id, "cancel": True})
        raise
    finally:
        with _lock:
            _pending.pop(request_id, None)


def call_sync(operation, payload, timeout=30):
    """For existing synchronous business readers; long inference uses async call."""
    if operation != 'status':
        raise ValueError('Only readiness is available to synchronous readers')
    request_id = uuid.uuid4().hex
    future = concurrent.futures.Future()
    with _lock:
        _pending[request_id] = future
    try:
        _send({'id': request_id, 'operation': operation, 'input': payload})
        return future.result(timeout)
    finally:
        with _lock:
            _pending.pop(request_id, None)


async def until_disconnected(request, operation):
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                raise asyncio.CancelledError()
            await asyncio.wait({task}, timeout=0.2)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
