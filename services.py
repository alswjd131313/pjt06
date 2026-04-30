import time
import json
import ast
import os
import re

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from openai import OpenAI

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMEDRIVER_PATH = os.path.join(BASE_DIR, "chromedriver-win64", "chromedriver.exe")


def _get_driver():
    service = Service(CHROMEDRIVER_PATH)
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    return webdriver.Chrome(service=service, options=options)


def _has_api_key():
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key and not key.startswith("여기에"))


def _run_llm(prompt):
    client = OpenAI(
        base_url="https://gms.ssafy.io/gmsapi/api.openai.com/v1",
        api_key=os.getenv("OPENAI_API_KEY"),
    )
    response = client.chat.completions.create(
        model=os.getenv("MODEL", "gpt-5-nano"),
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=4080,
    )
    return response.choices[0].message.content


# F103, F104: 토스증권 크롤링 (회사 검색 → 커뮤니티 댓글 수집)
def fetch_visible_comments(company_name, limit=20, max_scroll=10):
    driver = _get_driver()
    stock_name = company_name  # 실제 종목명 (크롤링 중 갱신)

    try:
        driver.get("https://www.tossinvest.com/")
        time.sleep(1)

        # 검색창 열기
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys("/")
        time.sleep(1)

        search_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[@placeholder='검색어를 입력해주세요']")
            )
        )
        search_input.send_keys(company_name)
        search_input.send_keys(Keys.ENTER)
        time.sleep(1)

        # 종목 페이지 진입 대기
        WebDriverWait(driver, 15).until(EC.url_contains("/order"))
        current_url = driver.current_url
        parts = current_url.split("/")
        stock_code = parts[parts.index("stocks") + 1]

        # 실제 종목명 파싱
        try:
            name_el = driver.find_element(By.CSS_SELECTOR, "h2.stock-name, h1.stock-name, [class*='stockName']")
            stock_name = name_el.text.strip() or company_name
        except Exception:
            stock_name = company_name

        # 커뮤니티 탭으로 이동
        community_url = f"https://www.tossinvest.com/stocks/{stock_code}/community"
        driver.get(community_url)
        time.sleep(2)

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#stock-content"))
            )
        except Exception:
            pass
        time.sleep(2)

        # 댓글 수집 (스크롤하며 누적)
        comments = []
        last_height = driver.execute_script("return document.body.scrollHeight")
        comment_selectors = [
            "div > div.tc3tm81 > div > div.tc3tm85 > span > span",
            "article.comment span",
            "#stock-content article span",
        ]

        for _ in range(max_scroll):
            for sel in comment_selectors:
                spans = driver.find_elements(By.CSS_SELECTOR, sel)
                if spans:
                    break

            for span in spans:
                text = span.text.strip()
                if text and text not in comments:
                    comments.append(text)

            if len(comments) >= limit:
                break

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    finally:
        driver.quit()

    return stock_name, comments[:limit]


# F105-1: LLM으로 부적절 댓글 필터링
def filter_inappropriate(comments):
    if not comments:
        return comments

    if not _has_api_key():
        return comments  # API 키 없으면 필터링 생략

    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(comments))
    prompt = f"""다음 댓글 목록에서 부적절한 댓글(욕설, 혐오, 비방, 선정성, 과도한 비난 등)의 번호만 반환해줘.
응답은 반드시 이 형식만: [0, 2, 5] 또는 []
다른 설명이나 마크다운 없이 배열만 반환.

댓글 목록:
{numbered}"""

    response = _run_llm(prompt).strip()
    try:
        to_remove = sorted(set(
            int(x) for x in json.loads(response)
            if isinstance(x, (int, float)) and 0 <= int(x) < len(comments)
        ), reverse=True)
        for i in to_remove:
            comments.pop(i)
    except Exception:
        pass

    return comments


# F105-2: pandas로 결측치·특수문자·IQR 이상치 제거
def clean_with_pandas(comments):
    if not comments:
        return [], {}

    df = pd.DataFrame(comments, columns=["comment"])
    df = df.dropna(subset=["comment"])
    df["comment"] = df["comment"].astype(str).str.strip()
    df = df[df["comment"] != ""]

    # 특수문자 제거 (한글·영문·숫자·공백만 유지)
    df["clean"] = df["comment"].apply(lambda x: re.sub(r"[^가-힣a-zA-Z0-9\s]", "", x))
    df["clean"] = df["clean"].str.replace(r"\s+", " ", regex=True).str.strip()

    # 무의미한 패턴 제거
    cond = (
        df["clean"].str.match(r"^\d+$") |
        df["clean"].str.match(r"^[ㅋㅎ]+$") |
        df["clean"].str.match(r"^[A-Za-z\s]+$") |
        (df["clean"].str.lower() == "none") |
        (df["clean"] == "")
    )
    df = df[~cond]

    # IQR 기반 길이 이상치 제거
    df["length"] = df["clean"].str.len()
    iqr_info = {}

    if len(df) >= 5:
        q1 = df["length"].quantile(0.25)
        q3 = df["length"].quantile(0.75)
        iqr = q3 - q1
        lower = max(5, q1 - 1.5 * iqr)
        upper = q3 + 1.5 * iqr
        iqr_info = {"Q1": q1, "Q3": q3, "IQR": iqr, "lower": lower, "upper": upper}
        df = df[(df["length"] >= lower) & (df["length"] <= upper)]
    else:
        df = df[df["length"] >= 3]

    return df["clean"].tolist(), iqr_info


# F106: LLM 텍스트 데이터 증강
def augment_comments(cleaned_comments):
    if not cleaned_comments:
        return []

    if not _has_api_key():
        return []  # API 키 없으면 증강 생략

    prompt = f"""{cleaned_comments}

위 리스트의 각각의 문장을, 의미는 유지하면서 다르게 표현해줘.
- 출력 형식: 대괄호 [ 로 시작해서 대괄호 ] 로 끝나는 파이썬 리스트
- 다른 설명 없이 리스트만 반환"""

    response = _run_llm(prompt).strip()

    if response.startswith("[오류"):
        return []

    try:
        augmented = ast.literal_eval(response)
        if isinstance(augmented, list):
            return augmented
    except (ValueError, SyntaxError):
        pass

    return []


# F110: 댓글 전체 요약
def summarize_comments(comments):
    if not comments:
        return ""

    if not _has_api_key():
        return ""  # API 키 없으면 요약 생략

    joined = "\n".join(f"- {c}" for c in comments)
    prompt = f"""다음은 주식 커뮤니티 댓글 목록입니다. 전체 내용을 3~5문장으로 요약해줘.

{joined}"""

    return _run_llm(prompt).strip()
