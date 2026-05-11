import os
import json
from datetime import datetime
import boto3
from botocore.client import Config

BUCKET = "ao3-raw"

def get_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

def ensure_bucket(client):
    existing = [b["Name"] for b in client.list_buckets()["Buckets"]]
    if BUCKET not in existing:
        client.create_bucket(Bucket=BUCKET)

def save_work(work: dict):
    client = get_client()
    ensure_bucket(client)
    key = f"works/{work['work_id']}.json"
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(work, default=str),
        ContentType="application/json",
    )

def load_work(work_id: int) -> dict:
    client = get_client()
    obj = client.get_object(Bucket=BUCKET, Key=f"works/{work_id}.json")
    return json.loads(obj["Body"].read())

def list_work_keys() -> list[str]:
    client = get_client()
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix="works/"):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys
