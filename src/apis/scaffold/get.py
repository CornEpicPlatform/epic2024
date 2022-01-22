# -*- coding: utf-8 -*-
# Time       : 2022/1/16 0:25
# Author     : QIN2DIM
# Github     : https://github.com/QIN2DIM
# Description:
from selenium.common.exceptions import WebDriverException

from services.bricklayer import Bricklayer
from services.explorer import Explorer
from services.settings import logger
from services.utils import CoroutineSpeedup, ToolBox

SILENCE = True

bricklayer = Bricklayer(silence=SILENCE)
explorer = Explorer(silence=SILENCE)


class SpawnBooster(CoroutineSpeedup):
    def __init__(self, docker=None, power: int = 4, debug: bool = False):
        super(SpawnBooster, self).__init__(docker=docker, power=power)

        self.debug = debug

    def control_driver(self, context, *args, **kwargs):
        ctx_cookies, url = context
        response = explorer.game_manager.is_my_game(ctx_cookies=ctx_cookies, page_link=url)

        # 启动 Bricklayer，获取免费游戏
        if response is False:
            logger.debug(ToolBox.runtime_report(
                motive="BUILD",
                action_name="SpawnBooster",
                message="正在为玩家领取免费游戏",
                progress=f"[{self.progress()}]",
                url=url
            ))
            try:
                bricklayer.get_free_game(ctx_cookies=ctx_cookies, page_link=url, refresh=False)
            except WebDriverException as e:
                if self.debug:
                    logger.exception(e)
                logger.error(ToolBox.runtime_report(
                    motive="QUIT",
                    action_name="SpawnBooster",
                    message="未知错误",
                    progress=f"[{self.progress()}]",
                    url=url
                ))


def join(trace: bool = False):
    """
    科技改变生活，一键操作，将免费商城搬空！

    :param trace:
    :return:
    """
    logger.debug(ToolBox.runtime_report(
        motive="BUILD",
        action_name="EpicGamer",
        message="正在为玩家领取免费游戏"
    ))

    """
    [🔨] 读取有效的身份令牌
    _______________
    """
    bricklayer.cookie_manager.refresh_ctx_cookies(verify=True)
    ctx_cookies = bricklayer.cookie_manager.load_ctx_cookies()

    """
    [🔨] 更新商城的免费游戏
    _______________
    """
    urls = explorer.game_manager.load_game_objs(only_url=True)
    if not urls:
        urls = explorer.discovery_free_games(ctx_cookies=ctx_cookies, save=True)

    """
    [🔨] 启动 Bricklayer，获取免费游戏
    _______________
    - 启动一轮协程任务，执行效率受限于本地网络带宽，若首轮报错频发请手动调低 `power` 参数。
    - 如果在命令行操作系统上运行本指令，执行效率受限于硬件性能。
    """
    docker = [[ctx_cookies, url] for url in urls]
    SpawnBooster(docker=docker, power=3, debug=trace).go()
