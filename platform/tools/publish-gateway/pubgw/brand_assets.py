"""Signed, post-import installation of per-survey brand assets.

LimeSurvey cannot import arbitrary theme files with an LSS/LSA archive.  The
plugin endpoint is therefore deliberately the only writer: this module binds
the exact JSON body digest into the existing per-engine channel signature and
never exposes materialized platform URLs or asset identifiers to the engine.
"""

import hashlib
import json
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from urllib.request import Request, urlopen

from .channel import PLUGIN_NAME, canonical_query, sign
from .model import SurveyDefinition


BRAND_ASSETS_FUNCTION = "brandAssetsV1"
BRAND_THEME = "zh-business"
BRAND_ASSETS_DIRECT_PATH = "/plugins/direct/plugin/{}/function/{}".format(
    PLUGIN_NAME, BRAND_ASSETS_FUNCTION
)
_RPC_SUFFIX = "/admin/remotecontrol"
_CHANNEL_SECRET = re.compile(r"\A[0-9a-f]{64}\Z")
_MATERIALIZED_FIELDS = frozenset({"url", "assetVersion", "contentType", "sizeBytes", "fileName", "sha256"})
_SLOT_FILE_NAMES = {
    ("logoAsset", "image/png"): "mjy-brand-logo.png",
    ("logoAsset", "image/jpeg"): "mjy-brand-logo.jpg",
    ("logoAsset", "image/gif"): "mjy-brand-logo.gif",
    ("logoAsset", "image/webp"): "mjy-brand-logo.webp",
    ("faviconAsset", "image/png"): "mjy-brand-favicon.png",
    ("faviconAsset", "image/jpeg"): "mjy-brand-favicon.jpg",
    ("faviconAsset", "image/gif"): "mjy-brand-favicon.gif",
    ("faviconAsset", "image/webp"): "mjy-brand-favicon.webp",
}
_TIMEOUT_SECONDS = 30

Post = Callable[[Request, int], bytes]


class BrandAssetError(RuntimeError):
    """A closed-world brand asset installation cannot safely continue."""


def validate_brand_assets(
    definition: SurveyDefinition, assets: Sequence[Mapping[str, Any]]
) -> Tuple[Mapping[str, Any], ...]:
    """Bind a byte bundle to the already-materialized branding definition.

    Request parsing has already verified bytes, base64 and digests.  This
    second check ensures the request cannot attach those otherwise-valid bytes
    to a different slot, MIME type, or materialized asset version snapshot.
    """
    branding = definition.branding
    if not isinstance(branding, Mapping):
        if assets:
            raise BrandAssetError("brand assets require a materialized branding definition")
        return ()

    expected = {}
    for slot in ("logoAsset", "faviconAsset"):
        materialized = branding.get(slot)
        if materialized is None:
            continue
        if not isinstance(materialized, Mapping) or set(materialized) != _MATERIALIZED_FIELDS:
            raise BrandAssetError("brand asset is not materialized")
        content_type = materialized.get("contentType")
        expected_name = _SLOT_FILE_NAMES.get((slot, content_type))
        if (
            expected_name is None
            or materialized.get("fileName") != expected_name
            or isinstance(materialized.get("assetVersion"), bool)
            or not isinstance(materialized.get("assetVersion"), int)
            or materialized["assetVersion"] < 1
            or isinstance(materialized.get("sizeBytes"), bool)
            or not isinstance(materialized.get("sizeBytes"), int)
            or materialized["sizeBytes"] < 1
        ):
            raise BrandAssetError("materialized brand asset metadata is invalid")
        expected[slot] = materialized

    actual = {}
    for item in assets:
        if not isinstance(item, Mapping):
            raise BrandAssetError("brand asset bundle item is invalid")
        slot = item.get("slot")
        if slot in actual or slot not in expected:
            raise BrandAssetError("brand asset bundle has an unexpected slot")
        materialized = expected[slot]
        if any(item.get(name) != materialized.get(name)
               for name in ("fileName", "contentType", "sizeBytes", "sha256")):
            raise BrandAssetError("brand asset bundle does not match materialized metadata")
        actual[slot] = item

    if set(actual) != set(expected):
        raise BrandAssetError("brand asset bundle does not cover materialized branding")
    return tuple(assets)


class BrandAssetInstaller:
    """Write a verified bundle through the single signed plugin POST endpoint."""

    def __init__(self, index_url: str, engine_instance_id: str, secret: str,
                 post: Optional[Post] = None, now: Optional[Callable[[], float]] = None):
        if not _CHANNEL_SECRET.fullmatch(secret or ""):
            raise BrandAssetError("brand asset channel secret is unavailable or malformed")
        self._index_url = index_url.rstrip("/")
        self._instance_id = engine_instance_id
        self._secret = secret
        self._post = post or _post
        if now is None:
            import time

            now = time.time
        self._now = now

    @classmethod
    def from_rpc_url(cls, rpc_url: str, engine_instance_id: str, secret: str,
                     post: Optional[Post] = None, now: Optional[Callable[[], float]] = None
                     ) -> Optional["BrandAssetInstaller"]:
        trimmed = rpc_url.rstrip("/")
        if not trimmed.endswith(_RPC_SUFFIX):
            return None
        return cls(trimmed[: -len(_RPC_SUFFIX)], engine_instance_id, secret, post=post, now=now)

    def install(self, survey_id: int, branding: Mapping[str, Any],
                assets: Sequence[Mapping[str, Any]]) -> None:
        if not assets:
            return
        if not isinstance(branding, Mapping) or branding.get("brandingVersion") != 1:
            raise BrandAssetError("compiled branding is unavailable")
        payload = {
            "version": 1,
            "theme": BRAND_THEME,
            "branding": dict(branding),
            "assets": [dict(asset) for asset in assets],
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        params = {
            "plugin": PLUGIN_NAME,
            "function": BRAND_ASSETS_FUNCTION,
            "sid": str(_positive(survey_id)),
            "bodySha256": hashlib.sha256(body).hexdigest(),
            "bodySize": str(len(body)),
            "ts": str(int(self._now())),
        }
        canonical = canonical_query(params)
        signature = sign(self._secret, params["ts"], canonical)
        # LimeSurvey's minimal CSRF exception matches this path-style route exactly.
        # The plugin endpoint deliberately still reads its closed whitelist from
        # $_GET, so plugin/function remain same-valued, HMAC-covered query keys.
        request = Request(
            "{}{}?{}&sig={}".format(self._index_url, BRAND_ASSETS_DIRECT_PATH, canonical, signature),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self._post(request, _TIMEOUT_SECONDS)
        except BrandAssetError:
            raise
        except Exception as error:
            # URLs include signatures and bodies include image bytes: neither may leak.
            raise BrandAssetError("brand asset channel unavailable ({})".format(type(error).__name__)) from None
        try:
            document = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BrandAssetError("brand asset channel returned invalid JSON") from None
        if document != {"status": "ok"}:
            raise BrandAssetError("brand asset channel refused the installation")


def _positive(value: Any) -> int:
    number = int(value)
    if number <= 0:
        raise BrandAssetError("survey id must be positive")
    return number


def _post(request: Request, timeout: int) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as error:
        raise BrandAssetError("brand asset channel transport failed ({})".format(
            type(error).__name__)) from None
