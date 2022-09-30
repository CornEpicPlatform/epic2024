# -*- coding: utf-8 -*-
# Time       : 2022/1/17 15:20
# Author     : QIN2DIM
# Github     : https://github.com/QIN2DIM
# Description:
import os
import random
import time
import typing
from hashlib import sha256
from urllib.request import getproxies

import cloudscraper
import hcaptcha_challenger as solver
import yaml
from hcaptcha_challenger.exceptions import ChallengePassed, ChallengeTimeout
from loguru import logger
from selenium.common.exceptions import (
    TimeoutException,
    ElementNotVisibleException,
    WebDriverException,
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    InvalidCookieDomainException,
)
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from services.settings import DIR_COOKIES, DIR_SCREENSHOT
from services.utils.toolbox import ToolBox, get_challenge_ctx, Challenger
from .exceptions import (
    AssertTimeout,
    UnableToGet,
    SwitchContext,
    PaymentBlockedWarning,
    AuthException,
    AuthMFA,
    AuthUnknownException,
    CookieRefreshException,
    LoginException,
)


class ArmorUtils:
    AUTH_SUCCESS = "success"
    AUTH_ERROR = "error"
    AUTH_CHALLENGE = "challenge"

    @staticmethod
    def fall_in_captcha_login(ctx, flag_url: str = None) -> typing.Optional[str]:
        """
        判断在登录时是否遇到人机挑战

        :param flag_url:
        :param ctx:
        :return: True：已进入人机验证页面，False：跳转到个人主页
        """
        flag_ = ctx.current_url if flag_url is None else flag_url

        logger.debug(
            ToolBox.runtime_report(
                action_name="ArmorUtils", motive="ARMOR", message="正在检测隐藏在登录界面的人机挑战..."
            )
        )

        ctx.switch_to.default_content()
        for _ in range(55):
            # {{< 檢測控制臺的附加信號 >}}
            try:
                mui_typography = ctx.find_elements(By.TAG_NAME, "h6")
                if len(mui_typography) > 1:
                    error_text = mui_typography[1].text.strip()
                    logger.error(
                        ToolBox.runtime_report(
                            action_name="ArmorUtils",
                            motive="ARMOR",
                            message="認證異常",
                            error_text=error_text,
                        )
                    )
                    if "账号或密码" in error_text:
                        raise LoginException(error_text)
                    raise AssertTimeout
            except (WebDriverException, AttributeError, TypeError):
                pass

            # {{< 頁面重定向|跳過挑戰 >}}
            try:
                WebDriverWait(ctx, 2).until(EC.url_changes(flag_))
                logger.debug(
                    ToolBox.runtime_report(
                        action_name="ArmorUtils", motive="ARMOR", message="🥤 跳过人机挑战"
                    )
                )
                return ArmorUtils.AUTH_SUCCESS
            except TimeoutException:
                pass

            # {{< 多因素判斷 >}}
            # 僅當前置條件滿足時，挑戰框架可見性斷言結果才有效
            try:
                WebDriverWait(ctx, 2, 0.1).until_not(EC.element_to_be_clickable((By.ID, "sign-in")))
            except TimeoutException:
                continue
            else:
                # {{< 挑戰框架可見 >}}
                try:
                    WebDriverWait(ctx, 2, 0.1).until(
                        EC.visibility_of_element_located((By.XPATH, ArmorKnight.HOOK_CHALLENGE))
                    )
                    return ArmorUtils.AUTH_CHALLENGE
                except TimeoutException:
                    pass

    @staticmethod
    def fall_in_captcha_runtime(ctx, window: str) -> typing.Optional[bool]:
        """
        判断在下单时是否遇到人机挑战

        # "//div[@id='talon_frame_checkout_free_prod']"
        :param ctx:
        :param window:
        :return:
        """
        try:
            if window == "free":
                ctx.switch_to.default_content()
                WebDriverWait(ctx, 5).until(
                    EC.frame_to_be_available_and_switch_to_it((By.XPATH, ArmorKnight.HOOK_PURCHASE))
                )
            WebDriverWait(ctx, 5, ignored_exceptions=(WebDriverException,)).until(
                EC.presence_of_element_located((By.XPATH, ArmorKnight.HOOK_CHALLENGE))
            )
            return True
        except TimeoutException:
            return False
        finally:
            if window == "free":
                ctx.switch_to.default_content()

    @staticmethod
    def face_the_checkbox(ctx: Chrome) -> typing.Optional[bool]:
        """遇见 hCaptcha checkbox"""
        try:
            WebDriverWait(ctx, 8, ignored_exceptions=(WebDriverException,)).until(
                EC.presence_of_element_located((By.XPATH, "//iframe[contains(@title,'checkbox')]"))
            )
            return True
        except TimeoutException:
            return False


class ArmorKnight(solver.HolyChallenger):
    """人机对抗模组"""

    # //iframe[@id='talon_frame_checkout_free_prod']
    HOOK_PURCHASE = "//div[@id='webPurchaseContainer']//iframe"

    def __init__(
        self, debug: typing.Optional[bool] = False, screenshot: typing.Optional[bool] = False
    ):
        super().__init__(debug=debug, screenshot=screenshot, lang="zh")
        self.critical_threshold = 3

    @property
    def utils(self):
        return ArmorUtils

    def switch_to_challenge_iframe(self, ctx, window: str):
        # turn to dom root
        ctx.switch_to.default_content()

        if window == "oms":
            WebDriverWait(ctx, 5).until(
                EC.presence_of_element_located((By.XPATH, self.HOOK_CHALLENGE))
            )
            hooks = ctx.find_elements(By.XPATH, self.HOOK_CHALLENGE)
            ctx.switch_to.frame(hooks[-1])
            return

        # purchase framework
        if window == "free":
            WebDriverWait(ctx, 5).until(
                EC.frame_to_be_available_and_switch_to_it((By.XPATH, self.HOOK_PURCHASE))
            )

        # challenge framework
        WebDriverWait(ctx, 5).until(
            EC.frame_to_be_available_and_switch_to_it((By.XPATH, self.HOOK_CHALLENGE))
        )

    def challenge_success(self, ctx: Challenger, window=None, **kwargs) -> typing.Tuple[str, str]:
        """
        判断挑战是否成功的复杂逻辑
        :param window:
        :param ctx: 挑战者驱动上下文
        :return:
        """

        def is_challenge_image_clickable():
            time.sleep(2)
            try:
                WebDriverWait(ctx, 1, 0.1).until(
                    EC.presence_of_element_located((By.XPATH, "//div[@class='task-image']"))
                )
            except TimeoutException:
                return False
            else:
                try:
                    prompts_obj = WebDriverWait(ctx, 2, 0.1).until(
                        EC.visibility_of_element_located((By.XPATH, "//div[@class='error-text']"))
                    )
                except TimeoutException:
                    pass
                else:
                    logger.warning(prompts_obj.text)

                # Is clickable
                return True

        flag = ctx.current_url

        # 首轮测试后判断短时间内页内是否存在可点击的拼图元素
        # hcaptcha 最多两轮验证，一般情况下，账号信息有误仅会执行一轮，然后返回登录窗格提示密码错误
        # 其次是被识别为自动化控制，这种情况也是仅执行一轮，回到登录窗格提示“返回数据错误”
        if is_challenge_image_clickable():
            return self.CHALLENGE_CONTINUE, "继续挑战"

        try:
            WebDriverWait(ctx, 2, 0.1).until(
                EC.visibility_of_element_located((By.XPATH, "//div[@class='error-text']"))
            )
            return self.CHALLENGE_RETRY, "重置挑战"
        except TimeoutException:
            ctx.switch_to.default_content()
            if window == "free":
                try:
                    WebDriverWait(ctx, 25, 0.1).until_not(
                        EC.presence_of_element_located((By.XPATH, self.HOOK_PURCHASE))
                    )
                    return self.CHALLENGE_SUCCESS, "退火成功"
                except TimeoutException:
                    return self.CHALLENGE_RETRY, "決策中斷"
            if window == "login":
                # {{< 人機挑戰|模擬退火 >}}
                for _ in range(45):
                    # 主動彈出挑戰框架 輪詢控制台回應
                    mui_typography = ctx.find_elements(By.TAG_NAME, "h6")

                    # {{< 檢測錯誤回復 >}}
                    # 1. 賬號信息錯誤 | 賬號被鎖定
                    # 2. 高威脅水平的訪客IP
                    if len(mui_typography) > 1:
                        try:
                            error_text = mui_typography[1].text.strip()
                        except AttributeError:
                            pass
                        else:
                            if "错误回复" in error_text:
                                self.critical_threshold -= 1
                                if self.critical_threshold == 0:
                                    self.log("原子實例被檢測", resp=error_text)
                                    raise CookieRefreshException(error_text)
                                return self.CHALLENGE_CRASH, "登入页面错误回复"
                            elif "there was a socket open error" in error_text:
                                return self.CHALLENGE_CRASH, "there was a socket open error"
                            else:
                                self.log("認證失敗", resp=error_text)
                                _unknown = AuthUnknownException(msg=error_text)
                                _unknown.report(error_text)
                                raise _unknown
                    # {{< 輪詢漏檢狀態 >}}
                    # 1. 回到挑戰框架 查看是否有漏檢挑戰項目
                    # 2. 檢測鏈接跳轉
                    else:
                        # {{< FluentAPI 判斷頁面跳轉 >}}
                        # 1. 如果没有遇到多重认证，人机挑战成功
                        # 2. 人机挑战通过，但可能还需处理 `2FA` 问题（超纲了）
                        try:
                            WebDriverWait(ctx, 2, 0.1).until(EC.url_changes(flag))
                        except TimeoutException:
                            pass
                        else:
                            if "id/login/mfa" not in ctx.current_url:
                                return self.CHALLENGE_SUCCESS, "退火成功"
                            raise AuthMFA("人机挑战已退出 error=遭遇意外的 2FA 双重认证")

                # 輪詢超時 若此時頁面仍未跳轉視爲挑戰失敗
                if ctx.current_url == flag:
                    return self.CHALLENGE_CONTINUE, "退火断言超时，挑战重置"
            if window == "oms":
                try:
                    WebDriverWait(ctx, 5).until(EC.url_changes(ctx.current_url))
                except TimeoutException:
                    pass
                return self.CHALLENGE_SUCCESS, "退火成功"

    def anti_hcaptcha(self, ctx, window: str = "login") -> typing.Union[bool, str]:
        """
        Handle hcaptcha challenge
        :param window: [login free]
        :param ctx:
        :return:
        """
        # [👻] 人机挑战！
        try:
            for _ in range(3):
                # [👻] 进入人机挑战关卡
                self.switch_to_challenge_iframe(ctx, window)
                # [👻] 获取挑战标签
                self.get_label(ctx)
                # [👻] 編排定位器索引
                self.mark_samples(ctx)
                # [👻] 拉取挑戰圖片
                self.download_images()
                # [👻] 滤除无法处理的挑战类别
                if drop := self.tactical_retreat(ctx) in [self.CHALLENGE_BACKCALL]:
                    return drop
                # [👻] 注册解决方案
                # 根据挑战类型自动匹配不同的模型
                model = self.switch_solution()
                # [👻] 識別|點擊|提交
                self.challenge(ctx, model=model)
                # [👻] 輪詢控制臺響應
                result, message = self.challenge_success(ctx, window=window)
                self.log("获取响应", desc=f"{message}({result})")
                if result in [self.CHALLENGE_SUCCESS, self.CHALLENGE_CRASH, self.CHALLENGE_RETRY]:
                    return result
        # 提交结果断言超时或 mark_samples() 等待超时
        except WebDriverException as err:
            logger.exception(err)
            return self.CHALLENGE_CRASH
        finally:
            ctx.switch_to.default_content()


class AssertUtils:
    """处理穿插在认领过程中意外出现的遮挡信息"""

    # 特征指令/简易错误
    # 此部分状态作为消息模板的一部分，尽量简短易理解
    COOKIE_EXPIRED = "💥 饼干过期了"
    ASSERT_OBJECT_EXCEPTION = "🚫 无效的断言对象"
    GAME_OK = "🎮 已在库"
    GAME_PENDING = "👀 待认领"
    GAME_CLAIM = "🛒 领取成功"
    GAME_NOT_FREE = "🦽 付费游戏"

    ONE_MORE_STEP = "🥊 进位挑战"

    @staticmethod
    def wrong_driver(ctx, msg: str):
        """判断当前上下文任务是否使用了错误的浏览器驱动"""
        if "chrome.webdriver" in str(ctx.__class__):
            raise SwitchContext(msg)

    @staticmethod
    def surprise_license(ctx) -> typing.Optional[bool]:
        """新用户首次购买游戏需要处理许可协议书"""
        try:
            surprise_obj = WebDriverWait(
                ctx, 3, ignored_exceptions=(ElementNotVisibleException,)
            ).until(EC.presence_of_element_located((By.XPATH, "//label[@for='agree']")))
        except TimeoutException:
            return
        else:
            try:
                if surprise_obj.text == "我已阅读并同意最终用户许可协议书":
                    # 勾选协议
                    tos_agree = WebDriverWait(
                        ctx, 3, ignored_exceptions=(ElementClickInterceptedException,)
                    ).until(EC.element_to_be_clickable((By.ID, "agree")))

                    # 点击接受
                    tos_submit = WebDriverWait(
                        ctx, 3, ignored_exceptions=(ElementClickInterceptedException,)
                    ).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[text()='接受']/parent::button"))
                    )
                    time.sleep(1)
                    tos_agree.click()
                    tos_submit.click()
                    return True
            # 窗口渲染出来后因不可抗力因素自然消解
            except (TimeoutException, StaleElementReferenceException):
                return

    @staticmethod
    def surprise_warning_purchase(ctx) -> typing.Optional[bool]:
        """
        处理弹窗遮挡消息。

        这是一个没有意义的操作，但无可奈何，需要更多的测试。
        :param ctx:
        :return:
        """
        time.sleep(1)

        try:
            WebDriverWait(ctx, 3).until(EC.visibility_of_element_located((By.XPATH, "//h1")))
        except TimeoutException:
            return True
        else:
            surprise_warning_objs = ctx.find_elements(By.XPATH, "//h1//span")
            surprise_warnings: typing.List[str] = [i.text for i in surprise_warning_objs]

            if "内容品当前在您所在平台或地区不可用。" in surprise_warnings:
                raise UnableToGet
            if (
                "本游戏包含成人内容，仅限17岁以上玩家选购" in surprise_warnings
                or "本游戏包含成人内容，仅限18岁以上玩家选购" in surprise_warnings
            ):
                WebDriverWait(ctx, 5, ignored_exceptions=(ElementClickInterceptedException,)).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[text()='继续']/parent::button"))
                ).click()
                return True
            return False

    @staticmethod
    def payment_auto_submit(ctx) -> typing.Optional[bool]:
        """认领游戏后订单自动提交 仅在常驻游戏中出现"""
        try:
            WebDriverWait(ctx, 5).until(
                EC.presence_of_element_located((By.XPATH, "//span[contains(text(),'感谢您的购买')]"))
            )
            return True
        except TimeoutException:
            return False

    @staticmethod
    def payment_blocked(ctx) -> typing.NoReturn:
        """判断游戏锁区"""
        # 需要在 webPurchaseContainer 里执行
        try:
            warning_text = (
                WebDriverWait(ctx, 3, ignored_exceptions=(WebDriverException,))
                .until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//h2[@class='payment-blocked__msg']")
                    )
                )
                .text
            )
            if warning_text:
                raise PaymentBlockedWarning(warning_text)
        except TimeoutException:
            pass

    @staticmethod
    def timeout(loop_start: float, loop_timeout: float = 180) -> typing.NoReturn:
        """任务超时锁"""
        if time.time() - loop_start > loop_timeout:
            raise AssertTimeout

    @staticmethod
    def purchase_status(
        ctx,
        page_link: str,
        get: bool,
        promotion2url: typing.Dict[str, str],
        action_name: typing.Optional[str] = "AssertUtils",
        init: typing.Optional[bool] = True,
    ) -> typing.Optional[str]:
        """
        断言当前上下文页面的游戏的在库状态。

        :param promotion2url:
        :param get:
        :param init:
        :param action_name:
        :param page_link:
        :param ctx:
        :return:
        """
        time.sleep(2)

        # 捕获按钮对象，根据按钮上浮动的提示信息断言游戏在库状态 超时的空对象主动抛出异常
        for _ in range(15):
            try:
                purchase_button = WebDriverWait(ctx, 2).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//button[@data-testid='purchase-cta-button']")
                    )
                )
                break
            except TimeoutException:
                if "再进行一步操作" in ctx.page_source:
                    return AssertUtils.ONE_MORE_STEP
        else:
            return AssertUtils.ASSERT_OBJECT_EXCEPTION

        # 游戏名 超时的空对象主动抛出异常
        game_name = promotion2url.get(page_link)

        # 游戏状态 在库|获取|购买
        purchase_msg = purchase_button.text

        if "已在" in purchase_msg:
            _message = "🛴 游戏已在库" if init else "🥂 领取成功"
            logger.info(
                ToolBox.runtime_report(
                    motive="GET", action_name=action_name, message=_message, game=f"『{game_name}』"
                )
            )
            return AssertUtils.GAME_OK if init else AssertUtils.GAME_CLAIM

        if "获取" in purchase_msg:
            deadline: typing.Optional[str] = None
            try:
                deadline = ctx.find_element(By.XPATH, "//span[contains(text(),'优惠截止于')]").text
            except (NoSuchElementException, AttributeError):
                pass

            # 必须使用挑战者驱动领取周免游戏，处理潜在的人机验证
            if deadline:
                AssertUtils.wrong_driver(ctx, "♻ 请使用挑战者上下文领取周免游戏。")
                if get is True:
                    message = f"💰 正在为玩家领取周免游戏 {deadline}"
                else:
                    message = f"🛒 添加至购物车 {deadline}"
            else:
                if get is True:
                    message = "🚀 正在为玩家领取免费游戏"
                else:
                    message = "🛒 添加至购物车"
            if init:
                logger.success(
                    ToolBox.runtime_report(
                        motive="GET",
                        action_name=action_name,
                        message=message,
                        game=f"『{game_name}』",
                    )
                )

            return AssertUtils.GAME_PENDING

        if "购买" in purchase_msg:
            logger.warning(
                ToolBox.runtime_report(
                    motive="SKIP",
                    action_name=action_name,
                    message="🚧 这不是免费游戏",
                    game=f"『{game_name}』",
                )
            )
            return AssertUtils.GAME_NOT_FREE

        return AssertUtils.ASSERT_OBJECT_EXCEPTION

    @staticmethod
    def refund_info(ctx):
        """
        处理订单中的 退款及撤销权信息

        :param ctx:
        :return:
        """
        try:
            WebDriverWait(ctx, 2, ignored_exceptions=(StaleElementReferenceException,)).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='我同意']/ancestor::button"))
            ).click()
            logger.debug("[🍜] 处理 UK 地区账号的「退款及撤销权信息」。")
        except TimeoutException:
            pass

    @staticmethod
    def unreal_resource_load(ctx):
        """等待虚幻商店月供资源加载"""
        pending_locator = [
            "//i[text()='添加到购物车']",
            "//i[text()='购物车内']",
            "//span[text()='撰写评论']",
        ] * 10

        time.sleep(3)
        for locator in pending_locator:
            try:
                WebDriverWait(ctx, 1).until(EC.element_to_be_clickable((By.XPATH, locator)))
                return True
            except TimeoutException:
                continue

    @staticmethod
    def unreal_surprise_license(ctx):
        try:
            WebDriverWait(ctx, 5).until(
                EC.presence_of_element_located((By.XPATH, "//span[text()='我已阅读并同意《最终用户许可协议》']"))
            ).click()
        except TimeoutException:
            pass
        else:
            WebDriverWait(ctx, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='接受']"))
            ).click()


class EpicAwesomeGamer:
    """白嫖人的基础设施"""

    # 操作对象参数
    URL_MASTER_HOST = "https://store.epicgames.com"
    URL_LOGIN_GAMES = "https://www.epicgames.com/id/login/epic?lang=zh-CN"
    URL_ACCOUNT_PERSONAL = "https://www.epicgames.com/account/personal"

    # 购物车结算成功
    URL_CART_SUCCESS = "https://store.epicgames.com/zh-CN/cart/success"

    URL_LOGIN_UNREAL = "https://www.unrealengine.com/id/login/epic?lang=zh-CN"
    URL_UNREAL_STORE = "https://www.unrealengine.com/marketplace/zh-CN/assets"
    URL_UNREAL_MONTH = (
        f"{URL_UNREAL_STORE}?count=20&sortBy=effectiveDate&sortDir=DESC&start=0&tag=4910"
    )

    AUTH_STR_GAMES = "games"
    AUTH_STR_UNREAL = "unreal"

    CLAIM_MODE_ADD = "add"
    CLAIM_MODE_GET = "get"
    ACTIVE_BINGO = "下单"

    # Talon Service Challenger
    armor = None

    def __init__(self, email: str, password: str):
        """定义了一系列领取免费游戏所涉及到的浏览器操作。"""
        # 实体对象参数
        self.action_name = "BaseAction"
        self.email, self.password = email, password

        # 驱动参数
        self.loop_timeout = 300

        # 注册挑战者
        self.armor = self.armor or ArmorKnight(debug=True, screenshot=False)
        self.assert_ = AssertUtils()

    # ======================================================
    # Reused Action Chains
    # ======================================================
    def _reset_page(self, ctx, page_link: str, ctx_cookies: typing.List[dict], auth_str: str):
        if auth_str == self.AUTH_STR_GAMES:
            ctx.get(self.URL_ACCOUNT_PERSONAL)
        elif auth_str == self.AUTH_STR_UNREAL:
            ctx.get(self.URL_UNREAL_STORE)

        for cookie_dict in ctx_cookies:
            try:
                ctx.add_cookie(cookie_dict)
            except InvalidCookieDomainException:
                pass

        ctx.get(page_link)

    @staticmethod
    def _move_product_to_wishlist(ctx):
        try:
            move_buttons = ctx.find_elements(By.XPATH, "//span[text()='移至愿望清单']")
        except NoSuchElementException:
            pass
        else:
            for button in move_buttons:
                try:
                    button.click()
                except WebDriverException:
                    continue

    @staticmethod
    def _switch_to_payment_iframe(ctx):
        payment_frame = WebDriverWait(
            ctx, 5, ignored_exceptions=(ElementNotVisibleException,)
        ).until(EC.presence_of_element_located((By.XPATH, ArmorKnight.HOOK_PURCHASE)))
        ctx.switch_to.frame(payment_frame)

    @staticmethod
    def _accept_agreement(ctx):
        try:
            WebDriverWait(ctx, 2, ignored_exceptions=(ElementClickInterceptedException,)).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[contains(@class,'payment-check-box')]")
                )
            ).click()
        except TimeoutException:
            pass

    @staticmethod
    def _click_order_button(ctx, timeout: int = 20) -> typing.Optional[bool]:
        try:
            time.sleep(0.5)
            WebDriverWait(
                ctx, timeout, ignored_exceptions=(ElementClickInterceptedException,)
            ).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'payment-btn')]"))
            ).click()
            return True
        # 订单界面未能按照预期效果出现，在超时范围内重试若干次。
        except TimeoutException:
            ctx.switch_to.default_content()
            return False

    def _duel_with_challenge(self, ctx, window="free") -> typing.Optional[bool]:
        """
        动态处理人机挑战
        :param ctx:
        :return: True挑战成功，False挑战失败/需要跳过，None其他信号
        """
        if self.armor.utils.fall_in_captcha_runtime(ctx, window):
            self.assert_.wrong_driver(ctx, "任务中断，请使用挑战者上下文处理意外弹出的人机验证。")
            try:
                resp = self.armor.anti_hcaptcha(ctx, window=window)
                self.captcha_runtime_memory(ctx, suffix=f"_{window}")
                return resp
            except (WebDriverException, ChallengePassed):
                pass

    # ======================================================
    # Business Action Chains
    # ======================================================

    def _activate_payment(self, api, mode="get", init_cart=None) -> typing.Optional[bool]:
        """激活游戏订单"""
        element_xpath = {
            self.CLAIM_MODE_GET: "//button[@data-testid='purchase-cta-button']",
            self.CLAIM_MODE_ADD: "//button[@data-testid='add-to-cart-cta-button']",
            self.ACTIVE_BINGO: "//span[text()='下单']/parent::button",
        }
        for _ in range(5):
            try:
                WebDriverWait(api, 5, ignored_exceptions=(ElementClickInterceptedException,)).until(
                    EC.element_to_be_clickable((By.XPATH, element_xpath[mode]))
                ).click()
                logger.debug(f"[🔖] 资源订单加载完毕 - mode={mode}")
                return True
            # 加载超时，继续测试
            except TimeoutException:
                # 购物车为空 无法点击下单按钮
                if mode == self.ACTIVE_BINGO and init_cart is False:
                    return
                logger.debug(f"[🔖] 资源订单加载超时 - mode={mode}")
                continue
            # 出现弹窗遮挡
            except ElementClickInterceptedException:
                try:
                    if self.assert_.surprise_warning_purchase(api) is True:
                        continue
                except UnableToGet:
                    return False

    def _handle_payment(self, ctx) -> None:
        """
        处理游戏订单

        逻辑过于复杂，需要重构。此处为了一套代码涵盖各种情况，做了很多妥协。
        需要针对 周免游戏的订单处理 设计一套执行效率更高的业务模型。

        # 连接错误
        ctx.find_element(By.XPATH, "//span[@class='payment-alert__content']"

        :param ctx:
        :return:
        """
        # [🍜] Switch to the [Purchase Container] iframe.
        try:
            self._switch_to_payment_iframe(ctx)
        except TimeoutException:
            try:
                warning_layouts = ctx.find_elements(By.XPATH, "//h1//span")
                for warning_layout in warning_layouts:
                    # Handle delayed loading of cookies.
                    warning_text = warning_layout.text
                    if warning_text == "依旧要购买吗":
                        return
                    # Handle Linux User-Agent Heterogeneous Services.
                    if warning_text == "设备不受支持":
                        ctx.find_element(By.XPATH, "//span[text()='继续']/parent::button").click()
                        return self._handle_payment(ctx)
            except NoSuchElementException:
                pass
        # [🍜] 判断游戏锁区
        self.assert_.payment_blocked(ctx)

        # [🍜] Ignore: Click the [Accept Agreement] confirmation box.
        self._accept_agreement(ctx)

        # [🍜] Click the [order] button.
        response = self._click_order_button(ctx)
        if not response:
            return

        # [🍜] 处理 UK 地区账号的「退款及撤销权信息」。
        self.assert_.refund_info(ctx)

        # [🍜] 捕获隐藏在订单中的人机挑战，仅在周免游戏中出现。
        self._duel_with_challenge(ctx)

        # [🍜] Switch to default iframe.
        ctx.switch_to.default_content()
        ctx.refresh()

    @staticmethod
    def captcha_runtime_memory(ctx, suffix: str = ""):
        _finger = os.path.join(DIR_SCREENSHOT, f"{int(time.time())}{suffix}")

        # 保存截图
        ctx.get_screenshot_as_file(f"{_finger}.png")

        # 保存源码
        content = ctx.execute_cdp_cmd("Page.captureSnapshot", {}).get("data", "")
        with open(f"{_finger}.mhtml", "w", newline="") as file:
            file.write(content)

    def _game_login_prerequisite_actions(self, ctx):
        """處理游戲賬號登陸的先決條件 甩開追蹤器"""
        _button_sign_text = "//span[contains(@class,'sign-text')]"
        _button_login_with_epic = "//div[@id='login-with-epic']"

        ctx.get("https://store.epicgames.com/zh-CN/")
        current_url = ctx.current_url

        if "再进行一步操作" in ctx.page_source:
            logger.warning("ONE MORE STEP CHALLENGE")
            return False

        try:
            sign_tag = WebDriverWait(ctx, 45).until(
                EC.presence_of_element_located((By.XPATH, _button_sign_text))
            )
            # UserData 生效，账号已登入
            if sign_tag.text != "登录":
                return True
            sign_tag.click()
            WebDriverWait(ctx, 10).until(EC.url_changes(current_url))
            WebDriverWait(ctx, 20).until(
                EC.presence_of_element_located((By.XPATH, _button_login_with_epic))
            ).click()
        except TimeoutException:
            return self._game_login_prerequisite_actions(ctx)
        else:
            WebDriverWait(ctx, 30).until(EC.url_contains("id/login/epic?"))

    @staticmethod
    def _reflect_features(ctx):
        url_claim = "https://store.epicgames.com/zh-CN/free-games"
        url_login = f"https://www.epicgames.com/id/login?lang=zh-CN&noHostRedirect=true&redirectUrl={url_claim}"

        button_sign = "//span[contains(@class,'sign-text')]"
        button_login_with_epic = "//div[@id='login-with-epic']"

        ctx.get(url_claim)
        try:
            sign_tag = WebDriverWait(ctx, 45).until(
                EC.presence_of_element_located((By.XPATH, button_sign))
            )
        except TimeoutException:
            if "再进行一步操作" in ctx.page_source:
                logger.warning("ONE MORE STEP CHALLENGE")
        else:
            # UserData 生效，账号已登入
            if sign_tag.text not in ["登录"]:
                return True
            ctx.get(url_login)
            try:
                WebDriverWait(ctx, 10).until(EC.url_changes(url_claim))
                WebDriverWait(ctx, 20).until(
                    EC.presence_of_element_located((By.XPATH, button_login_with_epic))
                ).click()
            except TimeoutException:
                return

    def login(self, email: str, password: str, ctx, auth_url: str):
        """
        作为被动方式，登陆账号，刷新 identity token。

        此函数不应被主动调用，应当作为 refresh identity token / Challenge 的辅助函数。
        :param auth_url:
        :param ctx:
        :param email:
        :param password:
        :return:
        """
        # IF True: in store page
        # ELSE: in login page
        if auth_url == self.URL_LOGIN_GAMES and self._reflect_features(ctx):
            return ArmorUtils.AUTH_SUCCESS
        if "id/login/epic" not in ctx.current_url:
            ctx.get(auth_url)

        time.sleep(random.uniform(3, 5))

        WebDriverWait(ctx, 10, ignored_exceptions=(ElementNotVisibleException,)).until(
            EC.presence_of_element_located((By.ID, "email"))
        ).send_keys(email)

        time.sleep(random.uniform(1, 2))

        WebDriverWait(ctx, 10, ignored_exceptions=(ElementNotVisibleException,)).until(
            EC.presence_of_element_located((By.ID, "password"))
        ).send_keys(password)

        time.sleep(random.uniform(1, 2))

        WebDriverWait(ctx, 60, ignored_exceptions=(ElementClickInterceptedException,)).until(
            EC.element_to_be_clickable((By.ID, "sign-in"))
        ).click()

        logger.debug(
            ToolBox.runtime_report(motive="MATCH", action_name=self.action_name, message="实体信息注入完毕")
        )

    def cart_success(self, ctx):
        """
        提高跳过人机挑战的期望，使用轮询的方式检测运行状态
        确保进入此函数时，已经点击 order 按钮，并已处理欧盟和新手协议，无任何遮挡。
        :param ctx:
        :return:
        """

        def annealing():
            logger.debug("[🎃] 退火成功")

            return True

        _fall_in_challenge = 0
        for _ in range(30):
            ctx.switch_to.default_content()
            try:
                payment_iframe = WebDriverWait(ctx, 0.5).until(
                    EC.presence_of_element_located((By.XPATH, ArmorKnight.HOOK_PURCHASE))
                )
            # 订单消失
            except TimeoutException:
                return annealing()
            else:
                try:
                    WebDriverWait(ctx, 0.5).until(EC.url_to_be(self.URL_CART_SUCCESS))
                    return annealing()
                except TimeoutException:
                    pass
                # 还原现场
                try:
                    ctx.switch_to.frame(payment_iframe)
                except WebDriverException:
                    return annealing()
                if _fall_in_challenge > 2:
                    return False
                # 进入必然存在的人机挑战框架
                try:
                    ctx.switch_to.frame(ctx.find_element(By.XPATH, ArmorKnight.HOOK_CHALLENGE))
                except WebDriverException:
                    continue
                else:
                    try:
                        ctx.find_element(By.XPATH, "//div[@class='prompt-text']")
                    except NoSuchElementException:
                        continue
                    else:
                        _fall_in_challenge += 1

    def cart_handle_payment(self, ctx):
        # [🍜] Switch to the [Purchase Container] iframe.
        try:
            self._switch_to_payment_iframe(ctx)
            logger.debug("[🌀] 切换至内联订单框架")
        except TimeoutException:
            ctx.switch_to.default_content()
            return

        # [🍜] Click the [order] button.
        logger.debug("[⚔] 激活人机挑战...")
        response = self._click_order_button(ctx, 5)
        if not response:
            return

        # [🍜] 处理 UK 地区账号的「退款及撤销权信息」。
        self.assert_.refund_info(ctx)

        # [🍜] 提高跳过人机挑战的期望，使用轮询的方式检测运行状态
        if not self.cart_success(ctx):
            # [🍜] 捕获隐藏在订单中的人机挑战，仅在周免游戏中出现。
            logger.debug("[⚔] 捕获隐藏在订单中的人机挑战...")
            self._duel_with_challenge(ctx)

        # [🍜] Switch to default iframe.
        logger.debug("[🌀] 弹出内联订单框架...")
        ctx.switch_to.default_content()
        ctx.refresh()

        return True

    def unreal_activate_payment(self, ctx, init=True):
        """从虚幻商店购物车激活订单"""
        # =======================================================
        # [🍜] 将月供内容添加到购物车
        # =======================================================
        try:
            offer_objs = ctx.find_elements(By.XPATH, "//i[text()='添加到购物车']")
            if len(offer_objs) == 0:
                raise NoSuchElementException
        # 不存在可添加内容
        except NoSuchElementException:
            # 商品在购物车
            try:
                hook_objs = ctx.find_elements(By.XPATH, "//i[text()='购物车内']")
                if len(hook_objs) == 0:
                    raise NoSuchElementException
                logger.debug(
                    ToolBox.runtime_report(
                        motive="PENDING", action_name=self.action_name, message="正在清空购物车"
                    )
                )
            # 购物车为空
            except NoSuchElementException:
                # 月供内容均已在库
                try:
                    ctx.find_element(By.XPATH, "//span[text()='撰写评论']")
                    _message = "本月免费内容均已在库" if init else "🥂 领取成功"
                    logger.success(
                        ToolBox.runtime_report(
                            motive="GET", action_name=self.action_name, message=_message
                        )
                    )
                    return AssertUtils.GAME_OK if init else AssertUtils.GAME_CLAIM
                # 异常情况：需要处理特殊情况，递归可能会导致无意义的死循环
                except NoSuchElementException:
                    return self.unreal_activate_payment(ctx, init=init)
        # 存在可添加的月供内容
        else:
            # 商品名
            offer_names = ctx.find_elements(By.XPATH, "//article//h3//a")
            # 商品状态：添加到购入车/购物车内/撰写评论(已在库)
            offer_buttons = ctx.find_elements(
                By.XPATH, "//div[@class='asset-list-group']//article//i"
            )
            offer_labels = [offer_button.text for offer_button in offer_buttons]
            # 逐级遍历将可添加的月供内容移入购物车
            for i, offer_label in enumerate(offer_labels):
                if offer_label == "添加到购物车":
                    offer_name = "null"
                    try:
                        offer_name = offer_names[i].text
                    except (IndexError, AttributeError):
                        pass
                    logger.debug(
                        ToolBox.runtime_report(
                            motive="PENDING",
                            action_name=self.action_name,
                            message="添加到购物车",
                            hook=f"『{offer_name}』",
                        )
                    )
                    offer_buttons[i].click()
                    time.sleep(1)
            time.sleep(1.5)

        # [🍜] 激活购物车
        try:
            ctx.find_element(By.XPATH, "//div[@class='shopping-cart']").click()
            logger.debug(
                ToolBox.runtime_report(
                    motive="HANDLE", action_name=self.action_name, message="激活购物车"
                )
            )
        except NoSuchElementException:
            ctx.refresh()
            time.sleep(2)
            return self.unreal_activate_payment(ctx)

        # [🍜] 激活订单
        try:
            WebDriverWait(ctx, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[text()='去支付']"))
            ).click()
            logger.debug(
                ToolBox.runtime_report(
                    motive="HANDLE", action_name=self.action_name, message="激活订单"
                )
            )
        except TimeoutException:
            ctx.refresh()
            time.sleep(2)
            return self.unreal_activate_payment(ctx, init=init)

        # [🍜] 处理首次下单的许可协议
        self.assert_.unreal_surprise_license(ctx)

        return AssertUtils.GAME_PENDING

    def unreal_handle_payment(self, ctx):
        # [🍜] Switch to the [Purchase Container] iframe.
        try:
            self._switch_to_payment_iframe(ctx)
        except TimeoutException:
            pass

        # [🍜] Click the [order] button.
        response = self._click_order_button(ctx)
        if not response:
            return

        # [🍜] 处理 UK 地区账号的「退款及撤销权信息」。
        self.assert_.refund_info(ctx)

        # [🍜] 捕获隐藏在订单中的人机挑战，仅在周免游戏中出现。
        self._duel_with_challenge(ctx)

        # [🍜] Switch to default iframe.
        ctx.switch_to.default_content()
        ctx.refresh()


class CookieManager(EpicAwesomeGamer):
    """管理上下文身份令牌"""

    def __init__(self, auth_str, email, password):
        super().__init__(email=email, password=password)

        self.action_name = "CookieManager"
        self.auth_str = auth_str
        self.path_ctx_cookies = os.path.join(DIR_COOKIES, "ctx_cookies.yaml")
        self.ctx_session = None

    def _t(self) -> str:
        return (
            sha256(f"{self.email[-3::-1]}{self.auth_str}".encode("utf-8")).hexdigest()
            if self.email
            else ""
        )

    def load_ctx_cookies(self) -> typing.Optional[typing.List[dict]]:
        """载入本地缓存的身份令牌"""
        if not os.path.exists(self.path_ctx_cookies):
            return []

        with open(self.path_ctx_cookies, "r", encoding="utf8") as file:
            data: dict = yaml.safe_load(file)

        ctx_cookies = data.get(self._t(), []) if isinstance(data, dict) else []
        if not ctx_cookies:
            return []

        logger.debug(
            ToolBox.runtime_report(
                motive="LOAD", action_name=self.action_name, message="Load context cookie."
            )
        )

        return ctx_cookies

    def save_ctx_cookies(self, ctx_cookies: typing.List[dict]) -> None:
        """在本地缓存身份令牌"""
        _data = {}

        if os.path.exists(self.path_ctx_cookies):
            with open(self.path_ctx_cookies, "r", encoding="utf8") as file:
                stream: dict = yaml.safe_load(file)
                _data = _data if not isinstance(stream, dict) else stream

        _data.update({self._t(): ctx_cookies})

        with open(self.path_ctx_cookies, "w", encoding="utf8") as file:
            yaml.dump(_data, file)

    def is_available_cookie(self, ctx_cookies: typing.Optional[typing.List[dict]] = None) -> bool:
        """检测 Cookie 是否有效"""
        ctx_cookies = self.load_ctx_cookies() if ctx_cookies is None else ctx_cookies
        if not ctx_cookies:
            return False

        headers = {
            "cookie": ToolBox.transfer_cookies(ctx_cookies),
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.42",
            "origin": "https://www.epicgames.com",
            "referer": "https://www.epicgames.com/",
        }

        scraper = cloudscraper.create_scraper()
        response = scraper.get(
            self.URL_ACCOUNT_PERSONAL, headers=headers, allow_redirects=False, proxies=getproxies()
        )
        if response.status_code == 200:
            return True
        return False

    def refresh_ctx_cookies(
        self, silence: bool = True, ctx_session=None, keep_live=None
    ) -> typing.Optional[bool]:
        """
        更新上下文身份信息，若认证数据过期则弹出 login 任务更新令牌。
        :param keep_live: keep actively to the challenger context
        :param ctx_session:
        :param silence:
        :return:
        """
        # {{< Check Context Cookie Validity >}}
        if self.is_available_cookie():
            logger.success(
                ToolBox.runtime_report(
                    motive="CHECK",
                    action_name=self.action_name,
                    message="The identity token is valid.",
                )
            )
            return True
        # {{< Done >}}

        # {{< Insert Challenger Context >}}
        logger.success(
            ToolBox.runtime_report(
                motive="MATCH",
                action_name="__Context__",
                message="🎮 启动挑战者上下文",
                ctx_session=bool(ctx_session),
            )
        )

        ctx = ctx_session or get_challenge_ctx(silence=silence)
        auth_url = self.URL_LOGIN_UNREAL
        if self.auth_str == self.AUTH_STR_GAMES:
            auth_url = self.URL_LOGIN_GAMES

        balance_operator = -1
        try:
            while balance_operator < 8:
                balance_operator += 1
                # Enter the account information and jump to the man-machine challenge page.
                result = self.login(self.email, self.password, ctx=ctx, auth_url=auth_url)
                # Assert if you are caught in a man-machine challenge.
                try:
                    if result not in [ArmorUtils.AUTH_SUCCESS]:
                        result = self.armor.utils.fall_in_captcha_login(ctx=ctx)
                except AssertTimeout:
                    balance_operator += 1
                    continue
                else:
                    # Skip Challenge.
                    if result == ArmorUtils.AUTH_SUCCESS:
                        break
                    # Winter is coming, so hear me roar!
                    elif result == ArmorUtils.AUTH_CHALLENGE:
                        resp = self.armor.anti_hcaptcha(ctx, window="login")
                        if resp == self.armor.CHALLENGE_SUCCESS:
                            break
                        elif resp == self.armor.CHALLENGE_REFRESH:
                            balance_operator -= 0.5
                        elif resp == self.armor.CHALLENGE_BACKCALL:
                            balance_operator -= 0.75
                        elif resp == self.armor.CHALLENGE_CRASH:
                            balance_operator += 0.5

            else:
                logger.critical(
                    ToolBox.runtime_report(
                        motive="MISS",
                        action_name=self.action_name,
                        message="Identity token update failed.",
                    )
                )
                return False
        except AuthException as err:
            raise err
        except ChallengeTimeout as error:
            logger.critical(
                ToolBox.runtime_report(
                    motive="SKIP", action_name=self.action_name, message=error.msg
                )
            )
            return False
        else:
            # Store contextual authentication information.
            if self.auth_str != "games":
                ctx.get(self.URL_LOGIN_UNREAL)
            self.save_ctx_cookies(ctx_cookies=ctx.get_cookies())
            return self.is_available_cookie(ctx_cookies=ctx.get_cookies())
        finally:
            if ctx_session is None:
                if not keep_live:
                    ctx.quit()
                else:
                    self.ctx_session = ctx
        # {{< Done >}}
