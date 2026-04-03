import time
import logging
from datetime import datetime

import httpx

from src.utils import retry_on_error

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.band.us"


class BandFetcher:
    def __init__(self, access_token: str, cutoff_date: str):
        self.token = access_token
        self.cutoff = datetime.strptime(cutoff_date, "%Y-%m-%d")
        self.client = httpx.Client(timeout=30.0)

    @retry_on_error(max_retries=3, backoff_base=1.0)
    def get_band_keys(self, target_names: list[str]) -> dict[str, str]:
        resp = self.client.get(f"{BASE_URL}/v2.1/bands", params={
            "access_token": self.token
        })
        resp.raise_for_status()
        data = resp.json()

        if data.get("result_code") != 1:
            raise Exception(f"Band API 에러: {data}")

        result = {}
        for band in data["result_data"]["bands"]:
            if band["name"] in target_names:
                result[band["name"]] = band["band_key"]

        missing = set(target_names) - set(result.keys())
        if missing:
            logger.warning(f"밴드를 찾을 수 없음: {missing}")

        return result

    @retry_on_error(max_retries=3, backoff_base=1.0)
    def _fetch_page(self, params: dict) -> dict:
        resp = self.client.get(f"{BASE_URL}/v2/band/posts", params=params)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result_code") != 1:
            raise Exception(f"Band API 에러: {data}")

        return data["result_data"]

    def fetch_all_posts(self, band_key: str) -> list[dict]:
        all_posts = []
        params = {
            "access_token": self.token,
            "band_key": band_key,
            "locale": "ko_KR",
        }

        while True:
            result_data = self._fetch_page(params)
            items = result_data.get("items", [])

            if not items:
                break

            stop_paging = False
            for post in items:
                created = datetime.fromtimestamp(post["created_at"] / 1000)

                if created < self.cutoff:
                    stop_paging = True
                    break

                all_posts.append(post)

            if stop_paging:
                break

            next_params = result_data.get("paging", {}).get("next_params")
            if not next_params:
                break

            params = next_params
            params["locale"] = "ko_KR"

            time.sleep(0.5)

        return all_posts

    def close(self):
        self.client.close()


if __name__ == "__main__":
    print("band_fetcher.py 로드 성공!")
    print("실제 테스트는 BAND_ACCESS_TOKEN 설정 후 가능합니다.")
