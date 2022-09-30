# -*- coding: utf-8 -*-
# Time       : 2022/1/16 0:27
# Author     : QIN2DIM
# Github     : https://github.com/QIN2DIM
# Description:
import logging
import os
import sys
import typing
import warnings
from collections import deque
from dataclasses import dataclass
from typing import List, Union, Dict, Optional
from urllib.request import getproxies

import cloudscraper
import requests
from bs4 import BeautifulSoup
from loguru import logger
from lxml import etree  # skipcq: BAN-B410 - Ignore credible sources
from selenium.webdriver import ChromeOptions
from undetected_chromedriver import Chrome as Challenger
from webdriver_manager.chrome import ChromeDriverManager

logging.getLogger("WDM").setLevel(logging.NOTSET)

warnings.filterwarnings("ignore", category=FutureWarning)


class ToolBox:
    """可移植的工具箱"""

    logger_tracer = deque()
    motion = None

    @staticmethod
    def runtime_report(action_name: str, motive: str = "RUN", message: str = "", **params) -> str:
        """格式化输出"""
        flag_ = f">> {motive} [{action_name}]"
        if message != "":
            flag_ += f" {message}"
        if params:
            flag_ += " - "
            flag_ += " ".join([f"{i[0]}={i[1]}" for i in params.items()])

        # feat(pending): 将系统级日志按序插入消息队列
        # ToolBox.logger_tracer.put(flag_)

        return flag_

    @staticmethod
    def transfer_cookies(
        api_cookies: Union[List[Dict[str, str]], str]
    ) -> Union[str, List[Dict[str, str]]]:
        """
        将 cookies 转换为可携带的 Request Header
        :param api_cookies: api.get_cookies() or cookie_body
        :return:
        """
        if isinstance(api_cookies, str):
            return [
                {"name": i.split("=")[0], "value": i.split("=")[1]} for i in api_cookies.split("; ")
            ]
        return "; ".join([f"{i['name']}={i['value']}" for i in api_cookies])

    @staticmethod
    def secret_email(email: str, domain: Optional[bool] = None) -> str:
        """去除敏感数据"""
        domain = True if domain is None else domain
        prefix, suffix = email.split("@")
        secrets_prefix = f"{prefix[0]}***{prefix[-1]}"
        return f"{secrets_prefix}@{suffix}" if domain else secrets_prefix

    @staticmethod
    def init_log(**sink_path):
        """初始化 loguru 日志信息"""
        event_logger_format = (
            "<g>{time:YYYY-MM-DD HH:mm:ss}</g> | "
            "<lvl>{level}</lvl> - "
            # "<c><u>{name}</u></c> | "
            "{message}"
        )
        logger.remove()
        logger.add(
            sink=sys.stdout,
            colorize=True,
            level="DEBUG",
            format=event_logger_format,
            diagnose=False,
        )
        if sink_path.get("error"):
            logger.add(
                sink=sink_path.get("error"),
                level="ERROR",
                rotation="1 week",
                encoding="utf8",
                diagnose=False,
            )
        if sink_path.get("runtime"):
            logger.add(
                sink=sink_path.get("runtime"),
                level="DEBUG",
                rotation="20 MB",
                retention="20 days",
                encoding="utf8",
                diagnose=False,
            )
        return logger

    @staticmethod
    def handle_html(url_, cookie: str = None, allow_redirects=False):
        headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/100.0.4896.75 Safari/537.36 Edg/100.0.1185.36"
        }
        if cookie is not None and isinstance(cookie, str):
            headers.update({"cookie": cookie})
        scraper = cloudscraper.create_scraper()
        response_ = scraper.get(url_, headers=headers, allow_redirects=allow_redirects)
        tree_ = etree.HTML(response_.content)
        return tree_, response_

    @staticmethod
    def gen_motion():
        def pull_motion():
            url = "https://github.com/QIN2DIM/hcaptcha-challenger/wiki/Motion"
            headers = {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27"
            }
            res = requests.get(url, headers=headers, proxies=getproxies())
            soup = BeautifulSoup(res.text, "html.parser")
            body = soup.find("div", id="wiki-body").find("p")
            return [i.split(",") for i in body.text.split("\n")][:200]

        ToolBox.motion = ToolBox.motion or pull_motion()
        return ToolBox.motion or pull_motion()


@dataclass
class DriverWrapper:
    silence: bool = False
    path: str = ""
    options = ChromeOptions()

    def __post_init__(self):
        self.options.headless = self.silence

        self.options.add_argument("--log-level=3")
        self.options.add_argument("--disable-software-rasterizer")

        # Unified Challenge Language
        os.environ["LANGUAGE"] = "zh"
        self.options.add_argument(f"--lang={os.getenv('LANGUAGE', '')}")

        # Hook to headful xvfb server
        if "linux" in sys.platform or self.silence:
            self.options.add_argument("--disable-setuid-sandbox")
            self.options.add_argument("--disable-gpu")
            self.options.add_argument("--no-sandbox")
            self.options.add_argument("--no-xshm")
            self.options.add_argument("--disable-dev-shm-usage")
            self.options.add_argument("--no-first-run")

        if self.silence:
            self.options.add_argument("--window-size=1920,1080")
            self.options.add_argument("--start-maximized")

        # - Use chromedriver cache to improve application startup speed
        # - Requirement: undetected-chromedriver >= 3.1.5.post4
        self.path = self.path or ChromeDriverManager().install()


def get_challenge_ctx(silence: typing.Optional[bool] = None) -> Challenger:
    """挑战者驱动 用于处理人机挑战"""
    driver_wrapper = DriverWrapper(silence=silence)
    options = driver_wrapper.options
    if "linux" in sys.platform:
        logger.info("Please use `xvfb` to empower the headful Chrome.")
        logger.info("CMD: xvfb-run python3 main.py claim")
        if silence:
            raise RuntimeError("Please use `xvfb` to empower the headful Chrome.")
    logging.debug(ToolBox.runtime_report("__Context__", "ACTIVATE", "🎮 激活挑战者上下文"))
    return Challenger(options=options, driver_executable_path=driver_wrapper.path)
