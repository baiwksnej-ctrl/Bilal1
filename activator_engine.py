# ============================================================
# activator_engine.py - Guest account activator
# SERVER: https://loginbp.ppmainecoonghj.com
# ============================================================
import asyncio
import aiohttp
import ssl
import json
import os
import sys
import random
import time
import uuid
from datetime import datetime

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# ============ PB2 IMPORT ============
PB2_AVAILABLE = False
MajorLoginRes_pb2 = None
PorTs_pb2 = None

try:
    # Add Pb2 folder to path
    pb2_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Pb2")
    if pb2_dir not in sys.path:
        sys.path.insert(0, pb2_dir)

    from Pb2 import MajoRLoGinrEs_pb2 as MajorLoginRes_pb2
    from Pb2 import PorTs_pb2
    PB2_AVAILABLE = True
except ImportError as e:
    print(f"[activator] Pb2 import failed: {e}")


# ============ CONSTANTS ============
AES_KEY = bytes([89,103,38,116,99,37,68,69,117,104,54,37,90,99,94,56])
AES_IV  = bytes([54,111,121,90,68,114,50,50,69,51,121,99,104,106,77,37])

DEVICES = [
    "Asus ASUS_I005DA", "SM-G998B", "CPH2095", "Pixel 6", "OnePlus 9 Pro",
    "Samsung Galaxy S23 Ultra", "Xiaomi 13 Pro", "Redmi Note 10 Pro",
    "Moto G100", "OPPO Find X3", "Vivo X60 Pro",
]

CARRIERS = [
    "Jio", "Airtel", "Vodafone Idea", "BSNL", "T-Mobile",
    "Verizon", "AT&T", "Orange", "Telenor",
]

GPUS = [
    "Adreno (TM) 640", "Mali-G78", "PowerVR Rogue",
    "Adreno 660", "Mali-G610", "Adreno 730",
]

RELEASE_VERSION = "OB55"
UNITY_USER_AGENT = "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"
UNITY_VERSION = "2022.3.47f1"

LOGIN_BASE = "https://loginbp.ppmainecoonghj.com"
OAUTH_BASE = "https://100067.connect.garena.com"


# ============ HELPERS ============
def _enc(plain: bytes) -> bytes:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(plain, AES.block_size))


def _varint(n):
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(fn, v):
    if isinstance(v, bool):
        return _varint((fn << 3) | 0) + _varint(1 if v else 0)
    if isinstance(v, int):
        return _varint((fn << 3) | 0) + _varint(v)
    if isinstance(v, (str, bytes)):
        d = v.encode("utf-8") if isinstance(v, str) else v
        return _varint((fn << 3) | 2) + _varint(len(d)) + d
    if isinstance(v, dict):
        sub = _pack(v)
        return _varint((fn << 3) | 2) + _varint(len(sub)) + sub
    return b""


def _pack(fields: dict) -> bytes:
    out = b""
    for k in sorted(fields.keys(), key=lambda x: int(x)):
        v = fields[k]
        fn = int(k)
        if isinstance(v, list):
            for item in v:
                out += _field(fn, item)
        else:
            out += _field(fn, v)
    return out


def _region_url(region):
    r = region.upper()
    if r == "IND":
        return "https://client.ind.freefiremobile.com"
    elif r in ("BR", "US", "SAC", "NA"):
        return "https://client.us.freefiremobile.com"
    return "https://clientbp.ggpolarbear.com"


# ============ STEP 1: ACCESS TOKEN ============
async def get_access_token(uid, password):
    url = f"{OAUTH_BASE}/oauth/guest/token/grant"
    headers = {
        "Host": "100067.connect.garena.com",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; SHADOW_X Build/TP1A.220624.014)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }
    data = {
        "uid": uid,
        "password": password,
        "response_type": "token",
        "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, headers=headers, data=data, ssl=False) as r:
                if r.status == 200:
                    j = await r.json()
                    return j.get("open_id"), j.get("access_token")
        return None, None
    except Exception as e:
        return None, None


# ============ STEP 2: MAJOR LOGIN ============
def build_major_login(access_token, open_id, lang="en"):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    model = random.choice(DEVICES)
    carrier = random.choice(CARRIERS)
    gpu = random.choice(GPUS)
    uid_str = f"Google|{uuid.uuid4()}"

    fields = {
        3: now, 4: "free fire", 5: 1, 7: "2.127.13",
        8: "Android OS 9 / API-28 (PI/rel.cjw.20220518.114133)", 9: "Handheld",
        10: carrier, 11: "WIFI", 12: 1334, 13: 750, 14: "300",
        15: "ARMv7 VFPv3 NEON VMH | 2400 | 2", 16: 1993, 17: gpu,
        18: "OpenGL ES 3.2", 19: uid_str, 20: "105.235.139.91",
        21: lang, 22: open_id, 23: "4", 24: "Handheld", 25: model,
        29: access_token, 30: 1, 41: carrier, 42: "WIFI",
        57: "7428b253defc164018c604a1ebbfebdf", 60: 32936, 61: 29430,
        62: 2479, 63: 900, 64: 30823, 65: 32936, 66: 30823, 67: 32936,
        73: 1, 74: "/data/app/com.dts.freefireth-PdeDnOilCSFn37p1AH_FLg==/lib/arm",
        76: 1, 77: "2087f61c19f57f2af4e7feff0b24d9d9|/data/app/com.dts.freefireth-PdeDnOilCSFn37p1AH_FLg==/base.apk",
        78: 3, 79: 1, 81: "32", 83: "2019118692", 86: "OpenGLES2",
        87: 16383, 88: 4, 92: 9075, 93: "android",
        94: "KqsHT5ZLWrYljNb5Vqh//yFRlaPHSO9NWSQsVvOmdhEEn7W+VHNUK+Q+fduA3ptNrGB0Ll0LRz3WW0jOwesLj6aiU7sZ40p8BfUE/FI/jzSTwRe2",
        95: 111227, 97: 1, 98: 1, 99: "4", 100: "4",
    }
    return _enc(_pack(fields))


async def major_login(access_token, open_id, lang="en"):
    url = f"{LOGIN_BASE}/MajorLogin"
    body = build_major_login(access_token, open_id, lang)

    headers = {
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Ga-Sv": "1789534056",
        "ReleaseVersion": RELEASE_VERSION,
        "User-Agent": UNITY_USER_AGENT,
        "X-GA": "v1 1",
        "X-Unity-Version": UNITY_VERSION,
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.post(url, headers=headers, data=body, ssl=ctx) as r:
                if r.status != 200:
                    return None, None, f"HTTP {r.status}"
                content = await r.read()

        if len(content) < 64:
            return None, None, "response too short"

        payload = content[64:]

        if PB2_AVAILABLE and MajorLoginRes_pb2:
            try:
                res = MajorLoginRes_pb2.MajorLoginRes()
                res.ParseFromString(payload)
                token = getattr(res, "token", "") or ""
                uid = getattr(res, "account_uid", "") or ""
                region = getattr(res, "region", "") or ""
                if not token:
                    return None, None, "no token in response"
                return token, str(uid), region or "auto"
            except Exception as e:
                return None, None, f"pb2 parse: {e}"

        return None, None, "no pb2 parser"
    except Exception as e:
        return None, None, f"exception: {e}"


# ============ STEP 3: CHOOSE REGION ============
async def choose_region(jwt, region):
    url = f"{LOGIN_BASE}/ChooseRegion"
    body = _enc(_pack({1: region.upper()}))

    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; M2101K7AG Build/SKQ1.210908.001)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "Authorization": f"Bearer {jwt}",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION,
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, data=body, headers=headers, ssl=ctx) as r:
                return r.status == 200
    except Exception:
        return False


# ============ STEP 4: GET LOGIN DATA ============
async def get_login_data(jwt, open_id, region, platform_type="8"):
    base = _region_url(region)
    host = base.replace("https://", "")
    url = f"{base}/GetLoginData"

    fields = {
        3: time.strftime("%Y-%m-%d %H:%M:%S"), 4: "free fire", 5: 1, 7: "1.126.15",
        8: "Android OS 10 / API-29 (QP1A.190711.020/1617006012)", 9: "Handheld",
        10: "T-Mobile", 11: "WIFI", 12: 1600, 13: 720, 14: "320",
        15: "ARM64 FP ASIMD AES | 2301 | 8", 16: 2799, 17: "PowerVR Rogue GE8320",
        18: "OpenGL ES 3.2 build 1.1@5425693", 19: f"Google|{uuid.uuid4()}",
        20: "8.8.8.8", 21: "en", 22: open_id, 23: platform_type, 24: "Handheld",
        25: "realme RMX2189", 26: region.upper(), 29: jwt, 30: 1,
        41: "T-Mobile", 42: "WIFI",
        57: "1ac4b80ecf0478a44203bf8fac6120f5", 60: 19799, 61: 1198, 62: 5056,
        64: 1430, 65: 19999, 66: 1198, 67: 19799, 70: 4, 73: 2,
        74: "/data/app/com.dts.freefireth-FFifmAAfKh0HbXBegWOzaxw==/lib/arm64", 76: 1,
        77: "4c322aeb56444feaa151d1ea91a8f7f2|/data/app/com.dts.freefireth-FFifmAAfKh0HbXBegWOzaxw==/base.apk",
        78: 6, 79: 2, 81: "64", 83: "2019120816", 86: "OpenGLES2", 87: 3071, 88: 8,
        90: "New York", 91: "NY", 92: 10001, 93: "3rd_party",
        94: "KqsHTw+Xui+7NiknuVG39jBvqfcBIE++vNayjgpDtOGFORTYgMixv5qmFWsOvq136YMoizYxRRPFTZxTOkFnCjln760=",
        95: 111207, 96: '{"cur_rate":null,"support_etc2":false}',
        97: 1, 99: "30", 100: "38", 102: "47504412000e085134",
    }
    body = _enc(_pack(fields))

    headers = {
        "Host": host,
        "User-Agent": UNITY_USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "deflate, gzip",
        "Authorization": f"Bearer {jwt}",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION,
        "Content-Type": "application/octet-stream",
        "X-Unity-Version": UNITY_VERSION,
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.post(url, headers=headers, data=body, ssl=ctx) as r:
                if r.status == 200:
                    return await r.read()
        return None
    except Exception:
        return None


def parse_login_data(data):
    if PB2_AVAILABLE and PorTs_pb2:
        try:
            res = PorTs_pb2.GetLoginData()
            res.ParseFromString(data)
            return {
                "online_ip_port": getattr(res, "Online_IP_Port", ""),
                "chat_ip_port": getattr(res, "AccountIP_Port", ""),
                "account_name": getattr(res, "AccountName", ""),
            }
        except Exception:
            pass
    return {}


# ============ FULL ACTIVATE ============
async def activate_one(account, semaphore, idx, total, on_progress=None):
    async with semaphore:
        result = {
            "uid": account["uid"],
            "name": account.get("name", f"ACC-{account['uid'][-4:]}"),
            "region": account.get("region", "auto"),
            "status": "failed",
            "message": "",
            "data": {},
        }

        try:
            # Step 1
            open_id, access_token = await get_access_token(account["uid"], account["password"])
            if not open_id or not access_token:
                result["message"] = "access_token failed"
                if on_progress:
                    on_progress(idx, total, result)
                return result

            # Step 2
            token, acc_id, reg_or_err = await major_login(access_token, open_id, "en")
            if not token:
                result["message"] = f"MajorLogin: {reg_or_err}"
                if on_progress:
                    on_progress(idx, total, result)
                return result

            detected = reg_or_err if reg_or_err and reg_or_err != "auto" else (account.get("region") or "IND")
            result["data"]["token"] = token
            result["data"]["account_id"] = acc_id
            result["data"]["detected_region"] = detected

            # Step 3
            await choose_region(token, detected)

            # Step 4
            ld = await get_login_data(token, open_id, detected)
            if ld:
                ports = parse_login_data(ld)
                result["data"].update(ports)
                result["status"] = "success"
                result["message"] = f"OK [{detected}]"
            else:
                result["status"] = "partial"
                result["message"] = f"MajorLogin OK, GetLoginData failed [{detected}]"

        except Exception as e:
            result["message"] = f"exception: {str(e)[:80]}"

        if on_progress:
            on_progress(idx, total, result)
        return result


async def activate_batch(accounts, max_concurrent=20, on_progress=None):
    if not accounts:
        return []

    sem = asyncio.Semaphore(max_concurrent)
    tasks = [
        activate_one(acc, sem, i + 1, len(accounts), on_progress)
        for i, acc in enumerate(accounts)
    ]
    return await asyncio.gather(*tasks, return_exceptions=True)


# ============ SYNC WRAPPER (used by bot.py) ============
def run_activation(accounts, max_concurrent=20, on_progress=None):
    """Synchronous wrapper for asyncio activation."""
    return asyncio.run(activate_batch(accounts, max_concurrent, on_progress))