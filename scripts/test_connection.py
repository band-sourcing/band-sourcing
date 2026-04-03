#!/usr/bin/env python3
"""
연결 테스트 스크립트.

사용법:
  python scripts/test_connection.py

Band API / WooCommerce API 연결을 각각 테스트합니다.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from woocommerce import API as WooAPI

from src.config import load_config

BAND_API_BASE = "https://openapi.band.us"


def test_band(access_token: str) -> bool:
    print("=== Band API 연결 테스트 ===")

    if not access_token:
        print("  ✗ BAND_ACCESS_TOKEN이 설정되지 않았습니다.")
        return False

    try:
        resp = requests.get(
            f"{BAND_API_BASE}/v2.1/bands",
            params={"access_token": access_token},
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            bands = data.get("result_data", {}).get("bands", [])
            print(f"  ✓ 연결 성공! 밴드 {len(bands)}개 발견")
            for band in bands:
                print(f"    - {band.get('name', '(이름없음)')}")
            return True
        else:
            print(f"  ✗ 실패: HTTP {resp.status_code}")
            print(f"    {resp.text[:200]}")
            return False

    except Exception as e:
        print(f"  ✗ 연결 오류: {e}")
        return False


def test_woocommerce(wc_config: dict) -> bool:
    print("\n=== WooCommerce API 연결 테스트 ===")

    url = wc_config.get("url")
    key = wc_config.get("consumer_key")
    secret = wc_config.get("consumer_secret")

    if not all([url, key, secret]):
        print("  ✗ WC_SITE_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET을 모두 설정하세요.")
        return False

    try:
        api = WooAPI(
            url=url,
            consumer_key=key,
            consumer_secret=secret,
            version="wc/v3",
            timeout=15,
        )

        resp = api.get("products", params={"per_page": 1})

        if resp.status_code == 200:
            total = resp.headers.get("X-WP-Total", "?")
            print(f"  ✓ 연결 성공! 등록 상품 수: {total}")
            return True
        else:
            print(f"  ✗ 실패: HTTP {resp.status_code}")
            print(f"    {resp.text[:200]}")
            return False

    except Exception as e:
        print(f"  ✗ 연결 오류: {e}")
        return False


def main():
    config = load_config()

    band_ok = test_band(config["band"].get("access_token"))
    wc_ok = test_woocommerce(config["woocommerce"])

    print("\n=== 결과 ===")
    print(f"  Band API:        {'✓ 성공' if band_ok else '✗ 실패'}")
    print(f"  WooCommerce API: {'✓ 성공' if wc_ok else '✗ 실패'}")

    if not (band_ok and wc_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
