from zlg.paths import resource_path
# -*- coding:utf-8 -*-
#  FunctionZlgCANList.py
#
#  ~~~~~~~~~~~~
#
#  ZLGCAN functional layer API
#
#  ~~~~~~~~~~~~
#
#  ------------------------------------------------------------------
#  Author : GW00220178 
#  Changer :  
#  Last change by Author: 20.08.2022
#  Last change by Changer: 
#  Language: Python 3.7.5
#  ------------------------------------------------------------------
#
import json
import threading
import time
from typing import Optional

from zlg.zlgcan import zlgCan
from zlg.logutil import Logs


class zlgCanFuncs():
    log = Logs()
    device = None
    chn = None
    log_time = time.time()
    _lifecycle_lock = threading.RLock()

    @classmethod
    def device_open(cls, dbc_path: str | None = None, *, hardware_periodic=None):
        if dbc_path is None:
            local_dbc = resource_path("config", "V71-006-014DB01 CAN DataBase for SC-CANFD_V4.7-0907.dbc")
            dbc_path = str(local_dbc if local_dbc.is_file() else resource_path("config", "Example.dbc"))
        with cls._lifecycle_lock:
            if cls.device is not None:
                raise RuntimeError("设备已打开。")
            cls.device = zlgCan(0, dbc_path, hardware_periodic=hardware_periodic)
            cls.chn = None

    @classmethod
    def _require_device(cls):
        device = cls.device
        if device is None:
            raise RuntimeError("请先打开设备。")
        return device

    @classmethod
    def chn_open(cls, chn: int) -> int:
        cls._require_device().chn_open(chn)
        return 1

    @classmethod
    def chn_get(cls) -> tuple[str, ...]:
        device = cls.device
        return tuple(map(str, device.channels)) if device is not None else ()

    @classmethod
    def chn_close(cls, chn: int):
        with cls._lifecycle_lock:
            cls._require_device().reset(chn)
            if cls.chn == chn:
                cls.chn = None

    @classmethod
    def chn_use(cls, chn: int):
        with cls._lifecycle_lock:
            if chn not in cls._require_device().channels:
                raise RuntimeError(f"通道 {chn} 未打开。")
            cls.chn = chn

    @classmethod
    def send_msg_by_name(cls, name: str, single_data: dict= {}, 
                    can_fd: bool= True, fd_brs: bool = False, cycle_time: int | None = 100, *,
                    channel: int | None = None, background: bool = False, send_count: int | None = -1) -> int:
        """
        发送信号，基于周立功，基于报文名
        :param name: 报文名
        :param single_data: 信号键对值
        :param can_fd: 是不是canfd，默认为fd
        :param fd_brs: can fd 加速，默认不加速
        :param cycle_time: 间隔毫秒数。None 使用当前硬件定时任务的周期或 DBC 默认周期
        :param channel: 显式发送通道，默认使用当前通道
        :param background: 是否在周期任务中提交首帧
        :param send_count: 正整数表示发送次数，-1 持续发送，None 更新已有任务并保留剩余次数
        :return: 
        """
        device = cls._require_device()
        device.single_send(cls.chn if channel is None else channel, name, single_data,
                           can_fd=can_fd, fd_brs=fd_brs, cycle_time=cycle_time,
                           background=background, send_count=send_count)
        period = cycle_time if cycle_time is not None else "当前任务或 DBC 默认周期"
        cls.log.info(f"发送报文{name}\n值为 {single_data}\n是否fd {can_fd}\n是否fd加速 {fd_brs}\n发送周期 {period}")
        if (cls.chn if channel is None else channel, name) in device.thread_messages:
            return 1
        return 0
    
    @classmethod
    def break_msg_by_name(cls, name) -> int:
        """
        停止发送信号，基于周立功，基于报文名
        :param name: 报文名
        :return: 
        """
        cls._require_device().single_send(cls.chn, name, single_data = {}, state = False)
        cls.log.info(f"取消发送报文{name}")
        if (cls.chn, name) in cls._require_device().thread_messages:
            return 0
        return 1

    @classmethod
    def send_msg_by_id(cls, id: str, single_data: dict= {},
                    can_fd: bool= True, fd_brs: bool = False, cycle_time: int = 100) -> int:
        """
        发送信号，基于周立功, 基于报文id
        :param id: 报文id
        :param single_data: 信号键对值
        :param can_fd: 是不是canfd，默认为fd
        :param fd_brs: can fd 加速，默认不加速
        :param cycle_time: 间隔时间单位 毫秒
        :return: 
        """
        name = cls._require_device().db.get_message_by_id(id).name
        cls._require_device().single_send(cls.chn, 
            name, single_data, state = True, can_fd = can_fd, fd_brs = fd_brs, cycle_time = cycle_time)
        cls.log.info(f"发送报文ID{id}\n值为 {single_data}\n是否fd {can_fd}\n是否fd加速 {fd_brs}\n发送速率 {cycle_time}")
        if (cls.chn, name) in cls._require_device().thread_messages:
            return 1
        return 0
    @classmethod
    def break_msg_by_id(cls, id) -> None:
        """
        发送信号，基于周立功，基于报文名
        :param id: 报文id
        :return: 
        """
        name = cls._require_device().db.get_message_by_id(id).name
        cls._require_device().single_send(cls.chn, name, single_data = {}, state = False)
        cls.log.info(f"取消发送报文{id}")
        if (cls.chn, name) in cls._require_device().thread_messages:
            return 0
        return 1
    
    @classmethod
    def send_once_msg_by_name(cls, name: str, single_name: str, single_value: int, isBreak: bool= True,
            can_fd: bool= True, fd_brs: bool = False, cycle_time: int = 100) -> int:
        """触发指定信号，随后置零。isBreak=False 时持续发送复位值。"""
        return int(cls._require_device().send_timed_trigger(
            cls.chn, name, {single_name: single_value}, can_fd=can_fd, fd_brs=fd_brs,
            cycle_time=cycle_time, stop_after=isBreak))

    @classmethod
    def send_once_msg_by_name_two_singal(cls, name: str, single_name: str, single_value: int,  single_name1: str, single_value1: int,isBreak: bool= True,
            can_fd: bool= True, fd_brs: bool = False, cycle_time: int = 100) -> int:
        """在同一帧触发两个信号，随后一同置零。"""
        return int(cls._require_device().send_timed_trigger(
            cls.chn, name, {single_name: single_value, single_name1: single_value1},
            can_fd=can_fd, fd_brs=fd_brs, cycle_time=cycle_time, stop_after=isBreak))
    # @classmethod
    # def send_once_msg_by_name(cls, name: str, single_name: list, single_value: list, isBreak: bool = True,
    #                           can_fd: bool = True, fd_brs: bool = False, cycle_time: int = 100) -> None:
    #     """
    #     发送一次信号，即关闭
    #     :param name: 报文名
    #     :param  single_name: 要发送的信号
    #     :param  single_value: 要设定的信号值
    #     :param  isBreak: 完成后是否取消发送该信号，默认取消
    #     :param  can_fd: 是不是canfd，默认为fd
    #     :param  fd_brs: can fd 加速，默认不加速
    #     :param  cycle_time: 间隔时间单位 毫秒
    #     :return:
    #     """
    #     tag = 1
    #     for i in range(len(single_name)):
    #         data = {single_name[i]: single_value[i]}
    #         data_zero = {single_name[i]: 0}
    #         cls.send_msg_by_name(name, data,
    #                              can_fd=can_fd, fd_brs=fd_brs, cycle_time=cycle_time)
    #     for i in range(len(single_name)):
    #         if not cls._require_device().thread_messages[name]['data'][single_name[i]] == single_value[i]:
    #             tag = 0
    #         time.sleep(cycle_time / 1000)
    #         cls.send_msg_by_name(name, data_zero,
    #                              can_fd=can_fd, fd_brs=fd_brs, cycle_time=cycle_time)
    #     for i in range(len(single_name)):
    #         if not cls._require_device().thread_messages[name]['data'][single_name[i]] == 0:
    #             tag = 0
    #     if isBreak:
    #         cls.break_msg_by_name(name)
    #         if name in cls._require_device().thread_messages.keys():
    #             tag = 0
    #
    #     return tag
        
    @classmethod
    def get_sending_by_id(cls, message_id: str, single_name: str) -> Optional[int]:
        """
        按照id查询当前已发送信号
        :param message_id: 报文id
        :param single_name: 查询结果为数字还是值
        :return: 信号值或者None
        """
        name = cls._require_device().db.get_message_by_id(message_id).name
        value = cls.__get_signal_value(cls._require_device().thread_messages, name, single_name)
        cls.log.info(f"查询{message_id}的{single_name}，值为：{value}")
        return value

    @classmethod
    def get_sending_by_name(cls, message_name: str, single_name: str)-> Optional[int]:
        """
        按照 报文名称 查询已发送信号
        :param message_name: 报文名称
        :param single_name: 查询结果为数字还是值
        :return: 信号值或者None
        """
        value = cls.__get_signal_value(cls._require_device().thread_messages, message_name, single_name)
        cls.log.info(f"查询{message_name}的{single_name}，值为：{value}")
        return value
    
    @staticmethod
    def __get_signal_value(thread_messages: dict, message_name: str, single_name: str)-> Optional[int]:
        """
        根据报文、信号名称查询在 已发送线程数据 的值
        :param thread_messages: 已发送线程数据
        :param message_name: 报文名称
        :param single_name: 查询结果为数字还是值
        :return: 信号值或者None
        """
        try:
            value= thread_messages[(zlgCanFuncs.chn, message_name)]['data'][single_name]
        except KeyError:
            value= None
        
        return value


    @classmethod
    def send_diag_configs_by_zlg(cls, configs_array: list) -> None:
        """
        写入配置字
        :param configs_array: 配置字列表，必须输数字格式的元素
        :return: None
        """
        cls._require_device().diag_seng_configs(cls.chn, configs_array)
        cls.log.info(f"写入配置字：{configs_array}")

    @classmethod
    def get_diag_configs_by_zlg(cls) -> list:
        """
        查询配置字
        :param configs_array: 配置字列表，必输数字格式的元素
        :return: None
        """
        cls._require_device().diag_send(cls.chn, [0x03, 0x22, 0xF1, 0xB1, 0x00, 0x00, 0x00, 0x00])
        time.sleep(1)
        configs_array = cls._require_device().diag_config_analysis_chars(cls.chn)
        cls.log.info(f"查询配置字：{configs_array}")
        return configs_array

    @classmethod
    def send_diag_by_zlg(cls, configs_array: list) -> None:
        """
        发送诊断
        :param configs_array: 诊断详情 比如发送22 F1 B1就是[0x03, 0x22, 0xF1, 0xB1, 0x00, 0x00, 0x00, 0x00]
        :return: None
        """
        cls._require_device().diag_send(cls.chn, configs_array)
        cls.log.info(f"发送诊断：{configs_array}")

    @classmethod
    def receive_diag_by_zlg(cls) -> list:
        """
        接收诊断
        :return: 报文详情或者None
        """
        result = cls._require_device().diag_receive(cls.chn)
        cls.log.info(f"接收诊断返回：{result}")
        return result

    @classmethod
    def zlg_close(cls):
        with cls._lifecycle_lock:
            if cls.device is not None:
                cls._require_device().close()
                cls.device = None
                cls.chn = None
                cls.log.info("设备已关闭。")

    @classmethod
    def request_format(cls, request: object) -> dict:
        return json.loads(request.json)

    @classmethod
    def receive_frames(cls, channel=None):
        return cls._require_device().get_receive_data(channel)

    @classmethod
    def can_log_start(cls, logfilename: Optional[str] = ""):
        cls._require_device().blf_start(logfilename)

    @classmethod
    def can_log_close(cls):
        cls._require_device().blf_stop()

    @classmethod
    def can_log_fail(cls, logfilename: Optional[str] = ""):
        device = cls._require_device()
        device.blf_start(logfilename)
        device.blf_stop()

    @classmethod
    def can_log_split(cls)-> bool:
        """
        按照固定时间间隔分隔保存can log
        :return: bool
        """
        now = time.time()
        if now - cls.log_time >= 14400:  # 14400:
            cls.log_time = now
            return True
        return False
    
    @classmethod
    def can_log_time(cls):

        while True:
            cls.can_log_start()
            

    
if __name__ == "__main__":
    zlgCanFuncs.device_open("../config/BAIC_3.0平台_IBUS_ICC_CANFD_V1.23_20260430.dbc")
    zlgCanFuncs.chn_open(0)
    zlgCanFuncs.chn_use(0)
    # zlgCanFuncs.send_msg_by_name('PEPS2',{'SysPowerMod': 2, 'SysPowerModVld': 1})
    # zlgCanFuncs.send_msg_by_name('GW_NM',can_fd=False)
    # time.sleep(10)
    zlgCanFuncs.send_once_msg_by_name("BDCU_SWS_0x287", 'SelRi', 1, True)
    # zlgCanFuncs.get_diag_configs_by_zlg()
    # zlgCanFuncs.send_diag_by_zlg([0x02, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
    # time.sleep(0.5)
    # print(zlgCanFuncs.receive_diag_by_zlg())
    time.sleep(2)
    print(zlgCanFuncs.receive_diag_by_zlg())
