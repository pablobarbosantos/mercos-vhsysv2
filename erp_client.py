"""Cliente HTTP compartilhado para o ERP app.pabloagro.cloud."""
import os, time, requests
from dotenv import load_dotenv

load_dotenv()

ERP_BASE_URL = os.getenv("ERP_BASE_URL", "https://app.pabloagro.cloud").rstrip("/")
ERP_USER     = os.getenv("ERP_USER", "pablo")
ERP_PASS     = os.getenv("ERP_PASS", "pablo123")

_token: str | None = None
_token_ts: float = 0
_TOKEN_TTL = 29 * 24 * 3600  # 29 dias (token válido 30)

_RETRY_ON  = {429, 500, 502, 503, 504}
_NO_RETRY  = {400, 401, 403, 404, 422}


def _login() -> str:
    r = requests.post(
        f"{ERP_BASE_URL}/api/auth/login",
        json={"usuario": ERP_USER, "senha": ERP_PASS},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def get_token() -> str:
    global _token, _token_ts
    if not _token or (time.time() - _token_ts) > _TOKEN_TTL:
        _token = _login()
        _token_ts = time.time()
    return _token


def headers() -> dict:
    return {"X-Auth-Token": get_token(), "Content-Type": "application/json"}


def request(method: str, path: str, *, params=None, json=None, max_tries=3) -> requests.Response:
    url = f"{ERP_BASE_URL}{path}"
    for tentativa in range(max_tries):
        try:
            r = requests.request(method, url, headers=headers(), params=params, json=json, timeout=30)
            if r.status_code == 401:
                global _token
                _token = None  # força re-login
                r = requests.request(method, url, headers=headers(), params=params, json=json, timeout=30)
            if r.status_code in _NO_RETRY or r.status_code < 400:
                return r
            if r.status_code in _RETRY_ON and tentativa < max_tries - 1:
                time.sleep(2 ** tentativa)
                continue
            return r
        except requests.RequestException:
            if tentativa < max_tries - 1:
                time.sleep(2 ** tentativa)
            else:
                raise
    return r  # type: ignore


def get(path: str, params=None) -> dict | list:
    r = request("GET", path, params=params)
    r.raise_for_status()
    return r.json()


def post(path: str, body: dict) -> dict:
    r = request("POST", path, json=body)
    r.raise_for_status()
    return r.json()


def patch(path: str, body: dict) -> dict:
    r = request("PATCH", path, json=body)
    r.raise_for_status()
    return r.json()
