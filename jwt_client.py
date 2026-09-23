# ============================================================
# jwt_client.py - Free Fire JWT engine
# SERVER: https://loginbp.ppmainecoonghj.com/MajorLogin
# Uses Pb2/*_pb2.py for protobuf encoding/decoding
# ============================================================
import base64
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ PB2 IMPORT ============
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from Pb2 import MajoRLoGinrEs_pb2
    PB2_OK = True
except Exception as e:
    print(f"[jwt_client] Pb2 import failed: {e}")
    PB2_OK = False


# ============ KEYS ============
AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV  = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])


def enc_aes(data: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(data, 16))


def dec_aes(data: bytes) -> bytes:
    return unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(data), 16)


# ============ PROTOBUF ENCODER (no blackboxprotobuf) ============
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


# ============ LOGIN META ============
LOGIN_META = {
    3: b"", 4: b"free fire", 5: 1, 7: b"2.133.6",
    8: b"Android OS 12 / API-31 (SP1A.210812.003/compiler03061504)",
    9: b"Handheld", 10: b"airtel", 11: b"CarrierDataNetwork",
    12: 1600, 13: 720, 14: b"300",
    15: b"ARM64 FP ASIMD AES | 2301 | 8", 16: 3806,
    17: b"PowerVR Rogue GE8320",
    18: b"OpenGL ES 3.2 build 1.13@5776728",
    19: b"", 20: b"223.228.74.9", 21: b"en", 22: b"",
    23: b"4", 24: b"Handheld", 25: b"vivo V2111", 26: b"IND",
    29: b"", 30: 1, 41: b"airtel", 42: b"4G",
    57: b"1ac4b80ecf0478a44203bf8fac6120f5",
    60: 47135, 61: 1993, 62: 3152, 64: 2337, 65: 47335,
    66: 1993, 67: 47135, 73: 2,
    74: b"/data/app/~~p3h_eiATw1cHfPQlvxjhqg==/com.dts.freefiremax-dPHcgnpmQTrWraOdRcpnuQ==/lib/arm64",
    76: 2,
    77: b"38f4751a330688ab124c2c804cec90a5|/data/app/~~p3h_eiATw1cHfPQlvxjhqg==/com.dts.freefiremax-dPHcgnpmQTrWraOdRcpnuQ==/base.apk",
    78: 2, 79: 2, 81: b"64", 83: b"2019118527",
    85: 3, 86: b"OpenGLES3", 87: 3071, 88: 4, 92: 18693,
    93: b"android_max",
    94: b"KqsHT4LSqizyvxLV1tQJmqyzRHnrvP+fGB4YT+fq1TcQwy54I81dQdSnVxi1nTRKBjb3jBdf3/qUnNKzRq8ew1CnRtKXgtMyC2U3m0L86eg35ovY",
    95: 111207,
    96: b'{"cur_rate":null,"support_etc2":true}',
    97: 1, 98: 1, 99: b"4", 100: b"4", 102: {},
    104: 2824, 105: 1,
    106: b"https://dl-tata.freefireind.in/live/ABHotUpdates/|https://core-tata.freefireind.in/live/ABHotUpdates/|211c933168f55902c7dfbfd8c4e2957d",
    107: b"c8e41b7a93f02d56e1a94c7b8203f5d1",
}


# ============ ENDPOINTS ============
OAUTH_BASE = "https://ffmconnect.live.gop.garenanow.com"
LOGIN_BASE = "https://loginbp.ppmainecoonghj.com"

CLIENT_ID     = 100067
CLIENT_SECRET = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"

UA_MSDK  = "GarenaMSDK/4.0.44(ASUS_AI2501_B ;Android 12;en;US;app 2.132.1 2019118525;)"
UA_UNITY = "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"


# ============ CLASS ============
class FreeFireLogin:
    def __init__(self, proxy=None, timeout=(5.0, 12.0)):
        self.proxy = proxy
        self.timeout = timeout

    def login(self, uid, password):
        grant = self._token_grant(uid, password)
        jwt, extra = self._major_login(grant["access_token"], grant["open_id"])
        if not jwt:
            raise RuntimeError("no JWT")

        acc = extra.get("account_id")
        if not acc:
            try:
                p = jwt.split(".")[1]
                p += "=" * (-len(p) % 4)
                payload = json.loads(base64.b64decode(p).decode("utf-8"))
                for k in ("account_id", "aid", "uid"):
                    if k in payload:
                        acc = payload[k]
                        break
            except Exception:
                pass
        if not acc:
            raise RuntimeError("no account_id")

        return {
            "uid": int(uid),
            "open_id": grant["open_id"],
            "access_token": grant["access_token"],
            "account_id": acc,
            "jwt": jwt,
        }

    def _token_grant(self, uid, password):
        body = {
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "client_type":   2,
            "device_id":     f"02-{uuid.uuid4()}",
            "password":      password,
            "response_type": "token",
            "uid":           int(uid),
        }
        for attempt in range(4):
            r = requests.post(
                f"{OAUTH_BASE}/api/v2/oauth/guest/token:grant",
                headers={
                    "User-Agent": UA_MSDK,
                    "Content-Type": "application/json; charset=utf-8",
                    "Connection": "close",
                },
                json=body, verify=False, proxies=self.proxy, timeout=self.timeout,
            )
            if r.status_code == 429:
                time.sleep(min(3 + attempt * 4, 20))
                continue
            if r.status_code == 200:
                d = (r.json() or {}).get("data") or {}
                if "access_token" in d and "open_id" in d:
                    return d
                raise RuntimeError("token_bad_payload")
            raise RuntimeError(f"token_http_{r.status_code}")
        raise RuntimeError("token_429")

    def _major_login(self, access_token, open_id):
        meta = dict(LOGIN_META)
        meta[3]  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S").encode()
        meta[19] = f"Google|{uuid.uuid4()}".encode()
        meta[22] = open_id.encode()
        meta[29] = access_token.encode()

        body = enc_aes(_pack(meta))

        for attempt in range(3):
            r = requests.post(
                f"{LOGIN_BASE}/MajorLogin",
                headers={
                    "Host": "loginbp.ppmainecoonghj.com",
                    "User-Agent": UA_UNITY,
                    "Accept": "*/*",
                    "Accept-Encoding": "deflate, gzip",
                    "Authorization": "Bearer",
                    "X-GA": "v1 1",
                    "ReleaseVersion": "OB55",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Unity-Version": "2018.4.12f1",
                    "X-GA-SV": str(int(time.time())),
                },
                data=body, verify=False, proxies=self.proxy, timeout=self.timeout,
            )
            if r.status_code == 429:
                time.sleep(min(3 + attempt * 4, 15))
                continue
            if r.status_code == 200:
                return self._parse(r.content)
            if "INVALID_PLATFORM" in r.text:
                raise RuntimeError("invalid_platform")
            raise RuntimeError(f"majorlogin_http_{r.status_code}")
        raise RuntimeError("majorlogin_429")

    @staticmethod
    def _parse(content):
        # Try decryption
        blobs = []
        try:
            blobs.append(dec_aes(content))
        except Exception:
            pass
        if len(content) > 64:
            try:
                blobs.append(dec_aes(content[64:]))
            except Exception:
                pass
            blobs.append(content[64:])
        blobs.append(content)

        # Try pb2 parsing
        if PB2_OK:
            for blob in blobs:
                try:
                    res = MajoRLoGinrEs_pb2.MajorLoginRes()
                    res.ParseFromString(blob)
                    token = res.token or ""
                    if token:
                        return token, {
                            "account_id": str(res.account_uid),
                            "region": res.region,
                        }
                except Exception:
                    pass

        # Fallback: regex for JWT
        for blob in blobs:
            m = re.search(rb"eyJ[\w\-]+\.[\w\-]+\.[\w\-]+", blob)
            if m:
                return m.group(0).decode(), {}
        return None, {}