# ============================================================
# like_engine.py - LikeProfile sender
# SERVER: https://clientbp.ppmainecoonghj.com/LikeProfile
# ============================================================
import os
import json
import time
import random
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3
import blackboxprotobuf

from jwt_client import FreeFireLogin, enc_aes, UA_UNITY

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ CONFIG ============
LIKE_URL = "https://clientbp.ppmainecoonghj.com/LikeProfile"
_LIKE_TYPEDEF = {"1": {"type": "int", "name": ""}}

MAX_RETRIES       = 3
BASE_BACKOFF      = 1.5
MAX_BACKOFF       = 20.0
RATE_LIMIT_PER_MIN = 90
CHECKPOINT_EVERY   = 25
PROXY_FILE        = os.environ.get("PROXY_FILE", "proxies.txt")


# ============ PROXY POOL ============
_PROXY_LIST = []
_PROXY_LOCK = threading.Lock()
_PROXY_LOADED = {"done": False}


def _load_proxies():
    global _PROXY_LIST
    if _PROXY_LOADED["done"]:
        return _PROXY_LIST
    env_px = os.environ.get("PROXY_URL")
    if env_px:
        _PROXY_LIST.append(env_px.strip())
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE) as f:
                for line in f:
                    p = line.strip()
                    if p and not p.startswith("#"):
                        _PROXY_LIST.append(p)
        except Exception:
            pass
    _PROXY_LOADED["done"] = True
    return _PROXY_LIST


def _pick_proxy():
    if not _PROXY_LIST:
        return None
    with _PROXY_LOCK:
        p = random.choice(_PROXY_LIST)
    return {"http": p, "https": p}


def get_proxy_count():
    _load_proxies()
    return len(_PROXY_LIST)


# ============ RATE LIMITER ============
class RateLimiter:
    def __init__(self, per_minute):
        self.capacity = per_minute
        self.tokens = float(per_minute)
        self.refill = per_minute / 60.0
        self.last = time.time()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill)
            self.last = now
            if self.tokens < 1:
                time.sleep((1 - self.tokens) / self.refill)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0


_rate_limiter = RateLimiter(RATE_LIMIT_PER_MIN)


class GlobalBackoff:
    def __init__(self):
        self.lock = threading.Lock()
        self.until = 0.0

    def trigger(self, s):
        with self.lock:
            self.until = max(self.until, time.time() + s)

    def wait(self):
        with self.lock:
            r = self.until - time.time()
        if r > 0:
            time.sleep(r)


_global_backoff = GlobalBackoff()


# ============ SESSION ============
_tls = threading.local()


def _session():
    if not hasattr(_tls, "s"):
        s = requests.Session()
        s.verify = False
        s.mount("http://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4))
        s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4))
        _tls.s = s
    return _tls.s


# ============ PAYLOAD ============
def build_like_payload(target_uid: int) -> bytes:
    raw = blackboxprotobuf.encode_message({"1": int(target_uid)}, _LIKE_TYPEDEF)
    return enc_aes(raw)


# ============ SEND ONE LIKE ============
def like_once(engine, uid, password, target_uid, retries=MAX_RETRIES):
    last_err = None
    t0 = time.time()
    for attempt in range(retries + 1):
        _rate_limiter.acquire()
        _global_backoff.wait()
        try:
            info = engine.login(uid, password)
            jwt = info["jwt"]
            acc_id = info.get("account_id")

            body = build_like_payload(target_uid)
            headers = {
                "Host": "clientbp.ppmainecoonghj.com",
                "User-Agent": UA_UNITY,
                "Accept": "*/*",
                "Accept-Encoding": "deflate, gzip",
                "X-GA-SV": str(int(time.time())),
                "Authorization": f"Bearer {jwt}",
                "X-GA": "v1 1",
                "ReleaseVersion": "OB55",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Unity-Version": "2018.4.12f1",
            }
            r = _session().post(
                LIKE_URL, headers=headers, data=body,
                timeout=20, verify=False, proxies=_pick_proxy(),
            )

            if r.status_code == 429:
                wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                _global_backoff.trigger(wait)
                last_err = "429"
                continue

            if r.status_code == 200:
                return {"uid": uid, "account_id": acc_id, "success": True,
                        "elapsed": time.time() - t0}

            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)[:80]

        if attempt < retries:
            time.sleep(min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF))

    return {"uid": uid, "success": False, "error": last_err or "unknown"}


# ============ CHECKPOINT ============
def _key(uid):
    return hashlib.md5(str(uid).encode()).hexdigest()[:12]


def _load_ckpt(path):
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_ckpt(path, keys):
    try:
        with open(path, "w") as f:
            json.dump(sorted(keys), f)
    except Exception:
        pass


def clear_checkpoint(target_uid):
    path = f"like_checkpoint_{target_uid}.json"
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


# ============ BATCH ============
def run_like_batch(accounts, target_uid, workers=5,
                   on_progress=None, stop_flag=None,
                   resume=True, checkpoint_file=None, failed_out=None):
    if not accounts:
        return 0, 0, []

    if checkpoint_file is None:
        checkpoint_file = f"like_checkpoint_{target_uid}.json"
    if failed_out is None:
        failed_out = f"failed_accounts_{target_uid}.json"

    seen, uniq = set(), []
    for a in accounts:
        u = str(a.get("uid"))
        if u and u not in seen:
            seen.add(u)
            uniq.append(a)
    accounts = uniq

    done_keys = _load_ckpt(checkpoint_file) if resume else set()
    if done_keys:
        accounts = [a for a in accounts if _key(a["uid"]) not in done_keys]
    if not accounts:
        return 0, 0, []

    _load_proxies()
    engine = FreeFireLogin()

    ok = 0
    fail = 0
    results = []
    done = 0
    total = len(accounts)

    def _w(a):
        return like_once(engine, a["uid"], a["password"], target_uid)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_w, a): a for a in accounts}
        for fut in as_completed(futs):
            if stop_flag and stop_flag.get("stop"):
                for f in futs:
                    f.cancel()
                break
            try:
                res = fut.result()
            except Exception as e:
                res = {"uid": "?", "success": False, "error": str(e)[:80]}
            done += 1
            results.append(res)
            if res.get("success"):
                ok += 1
                done_keys.add(_key(res["uid"]))
            else:
                fail += 1
            if on_progress:
                try:
                    on_progress(done, total, ok, fail, res)
                except Exception:
                    pass
            if done % CHECKPOINT_EVERY == 0:
                _save_ckpt(checkpoint_file, done_keys)

    _save_ckpt(checkpoint_file, done_keys)

    if failed_out:
        failed = [r for r in results if not r.get("success")]
        if failed:
            try:
                with open(failed_out, "w", encoding="utf-8") as f:
                    json.dump(failed, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    return ok, fail, results