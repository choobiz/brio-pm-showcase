from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings


@lru_cache(maxsize=1)
def r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint or f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key,
        aws_secret_access_key=settings.r2_secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def presign_put(key: str, content_type: str, expires: int = 900) -> str:
    return r2_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.r2_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presign_get(key: str, expires: int = 900) -> str:
    return r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket, "Key": key},
        ExpiresIn=expires,
    )


def presign_download(key: str, filename: str, expires: int = 900) -> str:
    """A presigned GET that saves under the document's CURRENT name.

    The stored object keeps the name it was uploaded with (it is in the key),
    so without this a renamed document would download under its old name.
    """
    safe = filename.replace('"', "").replace("\n", " ")
    return r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket, "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe}"'},
        ExpiresIn=expires,
    )


def presign_inline(key: str, content_type: str, filename: str | None = None,
                   expires: int = 900) -> str:
    """A presigned URL the browser RENDERS instead of saving.

    The plain `presign_get` returns whatever disposition the object was stored
    with, which for most of this bucket is a download. Forcing
    `inline` plus the real content type is what lets a PDF or an image open in a
    preview pane rather than landing in the user's Downloads folder — the whole
    point of being able to look at a drawing without leaving the project.
    """
    disp = "inline"
    if filename:
        safe = filename.replace('"', "").replace("\n", " ")
        disp = f'inline; filename="{safe}"'
    return r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket, "Key": key,
                "ResponseContentDisposition": disp,
                "ResponseContentType": content_type},
        ExpiresIn=expires,
    )


def put_bytes(key: str, data: bytes, content_type: str) -> None:
    """Server-side upload of an in-memory object (e.g. a generated PDF)."""
    r2_client().put_object(
        Bucket=settings.r2_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def get_bytes(key: str) -> bytes:
    """Download an object's bytes."""
    return r2_client().get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()


def object_exists(key: str) -> bool:
    """True if the object exists in the bucket."""
    try:
        r2_client().head_object(Bucket=settings.r2_bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def head_size(key: str) -> int | None:
    """Authoritative object size (ContentLength) in bytes, or None if missing."""
    try:
        resp = r2_client().head_object(Bucket=settings.r2_bucket, Key=key)
        cl = resp.get("ContentLength")
        return int(cl) if cl is not None else None
    except ClientError:
        return None


# Origins allowed to PUT/GET directly against R2 via presigned URLs.
_CORS_ORIGINS = [
    "https://brio-pm.example.com",
    "http://localhost:3000",
]


def ensure_bucket_cors() -> None:
    """Apply the CORS policy that lets the frontend upload/download directly
    against R2 with presigned URLs. Idempotent — safe to re-run."""
    r2_client().put_bucket_cors(
        Bucket=settings.r2_bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": _CORS_ORIGINS,
                    "AllowedMethods": ["GET", "PUT"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        },
    )
