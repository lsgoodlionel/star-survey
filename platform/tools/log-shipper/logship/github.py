"""把证据包提交到 GitHub 仓库（跨应用共用一个仓库）。

用 Contents API 直接提交文件，服务器上不需要装 git，也不需要克隆整个仓库。
路径按应用与日期分区，开发可直接按路径或 manifest 内容检索：

    incidents/<app>/<YYYY>/<MM>/<DD>/<incidentId>/manifest.json
    incidents/<app>/<YYYY>/<MM>/<DD>/<incidentId>/bundle.tar.gz

令牌只从环境变量读取，且绝不写入日志或异常信息。
"""

import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

_API_ROOT = "https://api.github.com"
_INCIDENT_ID_PATTERN = re.compile(r"^(\d{4})(\d{2})(\d{2})T")


class UploadError(RuntimeError):
    """上传失败；调用方应保留本地证据包等待下次重试。"""


@dataclass(frozen=True)
class UploadResult:
    directory: str
    uploaded: bool


class HttpClient:
    """极薄的 HTTP 封装，便于测试替换。可选双向 TLS 客户端证书。"""

    def __init__(self, client_certificate: Optional[str] = None):
        self._context = None
        if client_certificate:
            self._context = ssl.create_default_context()
            self._context.load_cert_chain(client_certificate)

    def put_json(self, url: str, payload: Dict, token: str) -> Dict:
        request = urllib.request.Request(
            url,
            method="PUT",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "mjy-logship",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise UploadError(f"HTTP {error.code} {error.reason}") from None
        except urllib.error.URLError as error:
            raise UploadError(f"network error: {error.reason}") from None


class GitHubUploader:
    def __init__(
        self,
        repository: str,
        token: str,
        http: Optional[HttpClient] = None,
        branch: str = "main",
        retries: int = 2,
        retry_delay_seconds: float = 2.0,
        dry_run: bool = False,
        client_certificate: Optional[str] = None,
    ):
        self._repository = repository
        self._token = token
        self._http = http or HttpClient(client_certificate)
        self._branch = branch
        self._retries = retries
        self._retry_delay_seconds = retry_delay_seconds
        self._dry_run = dry_run

    def upload(self, bundle_path: Path, manifest: Dict, app: str, incident_id: str) -> UploadResult:
        directory = self._directory(app, incident_id)
        if self._dry_run:
            return UploadResult(directory=directory, uploaded=False)

        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        summary = str(manifest.get("errorSummary", ""))[:72]
        message = f"{app}: {summary}".strip()

        self._put(f"{directory}manifest.json", manifest_bytes, message)
        self._put(f"{directory}bundle.tar.gz", Path(bundle_path).read_bytes(), message)
        return UploadResult(directory=directory, uploaded=True)

    def _put(self, path: str, content: bytes, message: str) -> None:
        url = f"{_API_ROOT}/repos/{self._repository}/contents/{path}"
        payload = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": self._branch,
            "path": path,
        }
        attempt = 0
        while True:
            try:
                self._http.put_json(url, payload, self._token)
                return
            except UploadError as error:
                if attempt >= self._retries:
                    raise UploadError(self._scrub(str(error))) from None
                attempt += 1
                time.sleep(self._retry_delay_seconds)

    def _scrub(self, message: str) -> str:
        return message.replace(self._token, "[REDACTED]") if self._token else message

    @staticmethod
    def _directory(app: str, incident_id: str) -> str:
        match = _INCIDENT_ID_PATTERN.match(incident_id)
        if match:
            year, month, day = match.groups()
        else:
            year, month, day = time.strftime("%Y %m %d", time.gmtime()).split()
        return f"incidents/{app}/{year}/{month}/{day}/{incident_id}/"
