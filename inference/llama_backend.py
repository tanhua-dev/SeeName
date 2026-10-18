"""In-process Qwen3-VL inference with the pinned llama-cpp-python MTMD binding."""
from __future__ import annotations

import base64
import importlib.util
import os
import threading
import time

from strings import T
from .interfaces import BackendStatus, InferenceResult, LoadedModel
from .local_models import LocalModelStore


class LlamaBackend:
    """Own one model, reusing it across requests and serializing native calls.

    Native code is imported on start, and weights are opened on load/describe.
    status/diagnostics never wait for inference or invoke the native context.
    A deadline requests native abort, then waits for the binding to return safely;
    it cannot forcibly interrupt file mapping or every vision-encoder operation.
    close requests abort before waiting, and frees resources only after the call
    has returned. Batch stop retains the existing finish-current-file behavior.
    """

    def __init__(self, store: LocalModelStore, *, context_size: int = 2048, accelerator=None):
        self.store = store
        self.context_size = context_size
        self.accelerator = accelerator
        self._gpu_ready = False
        self._device = 'CPU'
        self._lock = store.lock
        self._state_lock = threading.Lock()
        self._closing = threading.Event()
        self._library = None
        self._handler_class = None
        self._llm = None
        self._files = None
        self._size = 0
        self._ready = False
        self._busy = ''
        self._error = ''
        store.on_change = self.close

    def start(self, on_log=None, timeout: float = 120) -> bool:
        with self._lock:
            if self._closing.is_set():
                return False
            try:
                if self._library is None:
                    import llama_cpp
                    from llama_cpp.llama_multimodal import Qwen3VLChatHandler
                    self._library = llama_cpp
                    self._handler_class = Qwen3VLChatHandler
                self._gpu_ready = bool(self.accelerator and self.accelerator.load())
                self._ready = True
                self._error = ''
                return True
            except Exception as error:
                self._ready = False
                self._error = T('无法初始化本地推理库：%s') % error
                if on_log:
                    on_log(self._error)
                return False

    def _release(self):
        with self._state_lock:
            llm, self._llm = self._llm, None
            self._files = None
            self._size = 0
            self._device = 'CPU'
        if llm is not None:
            # The pinned Llama.close() closes its chat handler before the model.
            llm.close()

    def _load(self, model: str, deadline: float):
        if self._closing.is_set():
            raise RuntimeError(T('模型正在释放，请稍后重试。'))
        if time.monotonic() >= deadline:
            raise TimeoutError(T('模型操作超时'))
        if not self._ready and not self.start():
            raise RuntimeError(self._error)
        files = self.store.resolve(model)
        if self._llm is not None and files == self._files:
            return
        self._release()
        self._busy = 'loading'
        handler = None
        llm = None
        try:
            threads = min(8, max(1, (os.cpu_count() or 4) // 2))
            for gpu in ([True, False] if self._gpu_ready else [False]):
                try:
                    handler = self._handler_class(
                        mmproj_path=str(files.projector), use_gpu=gpu, verbose=False,
                        image_min_tokens=64, image_max_tokens=1024, force_reasoning=False)
                    llm = self._library.Llama(
                        model_path=str(files.model), chat_handler=handler,
                        n_ctx=self.context_size, n_gpu_layers=-1 if gpu else 0, n_threads=threads,
                        n_threads_batch=threads, n_batch=512, seed=42,
                        offload_kqv=gpu, op_offload=gpu,
                        verbose=False, use_mmap=True)
                    self._device = 'Vulkan' if gpu else 'CPU'
                    break
                except Exception as error:
                    if llm is not None:
                        llm.close()
                    elif handler is not None:
                        handler.close()
                    llm = handler = None
                    if not gpu or self._closing.is_set() or time.monotonic() >= deadline:
                        raise
                    self._gpu_ready = False
                    self.accelerator.error = 'Vulkan model initialization failed; using CPU: ' + str(error)
            if self._closing.is_set() or time.monotonic() >= deadline:
                raise TimeoutError(T('模型操作超时'))
            size = files.size_bytes
            with self._state_lock:
                self._llm, self._files, self._size = llm, files, size
        except Exception:
            if llm is not None:
                llm.close()
            elif handler is not None:
                handler.close()
            raise
        finally:
            self._busy = ''

    def load(self, model: str, timeout: float = 180) -> tuple[bool, str]:
        deadline = time.monotonic() + max(0, timeout)
        with self._lock:
            try:
                self._load(model, deadline)
                self._error = ''
                return True, ''
            except Exception as error:
                self._error = str(error)
                return False, self._error

    def describe(self, image: bytes, *, model: str, prompt: str,
                 timeout: float = 300, max_tokens: int = 64) -> InferenceResult:
        started = time.monotonic()
        try:
            return self._describe(image, model=model, prompt=prompt, timeout=timeout, max_tokens=max_tokens)
        except TimeoutError:
            raise
        except Exception as error:
            # Python-visible GPU failures can retry on CPU. Native process
            # crashes cannot be recovered in-process and are not claimed here.
            with self._lock:
                if not self._gpu_ready or self._closing.is_set():
                    raise
                self._release()
                self._gpu_ready = False
                self.accelerator.error = 'Vulkan inference failed; using CPU: ' + str(error)
            return self._describe(image, model=model, prompt=prompt,
                                  timeout=max(0, timeout - (time.monotonic() - started)), max_tokens=max_tokens)

    def _describe(self, image: bytes, *, model: str, prompt: str,
                  timeout: float, max_tokens: int) -> InferenceResult:
        started = time.monotonic()
        deadline = started + max(0, timeout)
        with self._lock:
            try:
                self._load(model, deadline)
            except Exception as error:
                self._error = str(error)
                raise
            expired = threading.Event()
            monitor = None
            monitor_stop = threading.Event()
            failed = True
            try:
                with self._state_lock:
                    if self._closing.is_set():
                        raise RuntimeError(T('模型正在释放，请稍后重试。'))
                    self._busy = 'describing'
                    llm = self._llm

                def watch_abort():
                    # The binding clears abort flags on entry to completion.
                    # Keep requesting abort until native code returns, covering
                    # close/deadline requests that arrive just before entry.
                    while not monitor_stop.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            expired.set()
                        if expired.is_set() or self._closing.is_set():
                            llm.abort()
                        monitor_stop.wait(min(.05, remaining) if remaining > 0 else .05)

                monitor = threading.Thread(target=watch_abort, daemon=True)
                monitor.start()
                response = llm.create_chat_completion(
                    messages=[{'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url':
                            'data:image/jpeg;base64,' + base64.b64encode(image).decode('ascii')}},
                        {'type': 'text', 'text': prompt}]}],
                    max_tokens=max_tokens, temperature=.2, top_p=.8, top_k=20)
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError(T('模型操作超时'))
                if self._closing.is_set():
                    raise RuntimeError(T('模型正在释放，请稍后重试。'))
                choice = response['choices'][0]
                if choice.get('finish_reason') == 'abort':
                    raise RuntimeError(T('模型推理已中止'))
                self._error = ''
                failed = False
                return InferenceResult(
                    choice['message'].get('content') or '', time.monotonic() - started,
                    int((response.get('usage') or {}).get('prompt_tokens') or 0), response)
            except Exception as error:
                if expired.is_set() or time.monotonic() >= deadline:
                    self._error = T('模型操作超时')
                    raise TimeoutError(self._error) from error
                if self._closing.is_set():
                    self._error = T('模型正在释放，请稍后重试。')
                    raise RuntimeError(self._error) from error
                self._error = str(error)
                raise
            finally:
                monitor_stop.set()
                if monitor is not None:
                    monitor.join()
                # An aborted image prefill can leave backend-specific context
                # state behind. Recreate it on retry rather than reusing it.
                if failed:
                    self._release()
                self._busy = ''

    def status(self) -> BackendStatus:
        with self._state_lock:
            models = ((LoadedModel(self._files.name, self._size, 0),)
                      if self._files is not None else ())
        return BackendStatus(self._ready, models)

    def diagnostics(self) -> dict:
        return dict(backend='llama.cpp', device=self._device, initialized=self._ready,
                    acceleration_error=self.accelerator.error if self.accelerator else '',
                    found=self._library is not None or importlib.util.find_spec('llama_cpp') is not None,
                    busy=self._busy, error=self._error, models_dir=self.store.directory(create=False))

    def close(self) -> None:
        self._closing.set()
        with self._state_lock:
            if self._llm is not None and self._busy == 'describing':
                self._llm.abort()
        with self._lock:
            try:
                self._release()
            finally:
                self._ready = False
                self._gpu_ready = False
                self._busy = ''
                self._error = ''
                self._closing.clear()
