#!/usr/bin/env python3
"""WC 카테고리 ID 조회 스크립트. 서버에서 실행."""

import os
import sys
from dotenv import load_dotenv
from woocommerce import API

load_dotenv()

api = API(
    url=os.getenv("WC_SITE_URL"),
    consumer_key=os.getenv("WC_CONSUMER_KEY"),
    consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
    version="wc/v3",
    timeout=30,
)

resp = api.get("products/categories", params={"per_page": 100})
if resp.status_code != 200:
    print(f"ERROR: {resp.status_code} {resp.text}")
    sys.exit(1)

cats = resp.json()
print(f"\n총 {len(cats)}개 카테고리:\n")
print(f"{'ID':>5}  {'Parent':>6}  {'Name':<30}  {'Slug':<30}")
print("-" * 75)
for c in sorted(cats, key=lambda x: x["id"]):
    print(f"{c['id']:>5}  {c['parent']:>6}  {c['name']:<30}  {c['slug']:<30}")
