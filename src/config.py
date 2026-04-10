import os
import yaml
from dotenv import load_dotenv

def load_config() -> dict:
    load_dotenv()

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Band API (레거시 - 미사용)
    config["band"]["access_token"] = os.getenv("BAND_ACCESS_TOKEN")
    config["band"]["client_id"] = os.getenv("BAND_CLIENT_ID")
    config["band"]["client_secret"] = os.getenv("BAND_CLIENT_SECRET")

    # Band Playwright 크롤링용
    config["band"]["naver_id"] = os.getenv("NAVER_ID", "")
    config["band"]["naver_pw"] = os.getenv("NAVER_PW", "")

    config["woocommerce"] = {
        "url": os.getenv("WC_SITE_URL"),
        "consumer_key": os.getenv("WC_CONSUMER_KEY"),
        "consumer_secret": os.getenv("WC_CONSUMER_SECRET"),
    }

    config["wordpress"] = {
        "username": os.getenv("WP_USERNAME"),
        "app_password": os.getenv("WP_APP_PASSWORD"),
    }

    # 이미지 없는 상품 처리: "skip" | "placeholder" | "register"
    config["no_image_mode"] = os.getenv("NO_IMAGE_MODE", "register")

    return config


if __name__ == "__main__":
    config = load_config()
    print("설정 로드 성공!")
    print(f"  타겟 밴드: {config['band']['target_bands']}")
    print(f"  WC URL: {config['woocommerce']['url']}")
    print(f"  자동삭제: {config['auto_delete']['enabled']}")
    print(f"  최대 상품수: {config['auto_delete']['max_products']}")
    print(f"  네이버 ID: {'설정됨' if config['band']['naver_id'] else '미설정'}")
    print(f"  이미지없음 모드: {config['no_image_mode']}")
