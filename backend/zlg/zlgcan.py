from zlg.native import load_vendor_library, configure_zcan
from zlg.paths import data_path
# -*- coding:utf-8 -*-
#  zlgcan.py
#
#  ~~~~~~~~~~~~
#
#  ZLGCAN API
#
#  ~~~~~~~~~~~~
#
#  ------------------------------------------------------------------
#  Author : guochuangjian  
#  Changer : GW00220178 
#  Last change by Author: 21.02.2019
#  Last change by Changer: 20.08.2022
#  Language: Python 3.7.5
#  ------------------------------------------------------------------
#
import binascii
from ctypes import *
import json
from itertools import count
import os
import platform
from pathlib import Path
from collections import Counter, deque
import re
import struct
import threading
import time
from typing import Optional

import cantools
from cantools.database import Message
from can.io import BLFWriter
from can import Message as can_Message
from zlg.FunctionRely import FunctionRely
from zlg.backend import CanBackend, LoopbackBackend, validate_channel
from zlg.wake import encode_wake_message
from zlg.periodic import HardwarePeriodicSend, PeriodicSend, PulseSend
from zlg.tx_timing import TxSample, TxTiming

from zlg.logutil import Logs




ZCAN_DEVICE_TYPE = c_uint

INVALID_DEVICE_HANDLE  = 0
INVALID_CHANNEL_HANDLE = 0

'''
设备类型
'''
ZCAN_PCI5121          = ZCAN_DEVICE_TYPE(1) 
ZCAN_PCI9810          = ZCAN_DEVICE_TYPE(2) 
ZCAN_USBCAN1          = ZCAN_DEVICE_TYPE(3) 
ZCAN_USBCAN2          = ZCAN_DEVICE_TYPE(4) 
ZCAN_PCI9820          = ZCAN_DEVICE_TYPE(5) 
ZCAN_CAN232           = ZCAN_DEVICE_TYPE(6) 
ZCAN_PCI5110          = ZCAN_DEVICE_TYPE(7) 
ZCAN_CANLITE          = ZCAN_DEVICE_TYPE(8) 
ZCAN_ISA9620          = ZCAN_DEVICE_TYPE(9) 
ZCAN_ISA5420          = ZCAN_DEVICE_TYPE(10)
ZCAN_PC104CAN         = ZCAN_DEVICE_TYPE(11)
ZCAN_CANETUDP         = ZCAN_DEVICE_TYPE(12)
ZCAN_CANETE           = ZCAN_DEVICE_TYPE(12)
ZCAN_DNP9810          = ZCAN_DEVICE_TYPE(13)
ZCAN_PCI9840          = ZCAN_DEVICE_TYPE(14)
ZCAN_PC104CAN2        = ZCAN_DEVICE_TYPE(15)
ZCAN_PCI9820I         = ZCAN_DEVICE_TYPE(16)
ZCAN_CANETTCP         = ZCAN_DEVICE_TYPE(17)
ZCAN_PCIE_9220        = ZCAN_DEVICE_TYPE(18)
ZCAN_PCI5010U         = ZCAN_DEVICE_TYPE(19)
ZCAN_USBCAN_E_U       = ZCAN_DEVICE_TYPE(20)
ZCAN_USBCAN_2E_U      = ZCAN_DEVICE_TYPE(21)
ZCAN_PCI5020U         = ZCAN_DEVICE_TYPE(22)
ZCAN_EG20T_CAN        = ZCAN_DEVICE_TYPE(23)
ZCAN_PCIE9221         = ZCAN_DEVICE_TYPE(24)
ZCAN_WIFICAN_TCP      = ZCAN_DEVICE_TYPE(25)
ZCAN_WIFICAN_UDP      = ZCAN_DEVICE_TYPE(26)
ZCAN_PCIe9120         = ZCAN_DEVICE_TYPE(27)
ZCAN_PCIe9110         = ZCAN_DEVICE_TYPE(28)
ZCAN_PCIe9140         = ZCAN_DEVICE_TYPE(29)
ZCAN_USBCAN_4E_U      = ZCAN_DEVICE_TYPE(31)
ZCAN_CANDTU_200UR     = ZCAN_DEVICE_TYPE(32)
ZCAN_CANDTU_MINI      = ZCAN_DEVICE_TYPE(33)
ZCAN_USBCAN_8E_U      = ZCAN_DEVICE_TYPE(34)
ZCAN_CANREPLAY        = ZCAN_DEVICE_TYPE(35)
ZCAN_CANDTU_NET       = ZCAN_DEVICE_TYPE(36)
ZCAN_CANDTU_100UR     = ZCAN_DEVICE_TYPE(37)
ZCAN_PCIE_CANFD_100U  = ZCAN_DEVICE_TYPE(38)
ZCAN_PCIE_CANFD_200U  = ZCAN_DEVICE_TYPE(39)
ZCAN_PCIE_CANFD_400U  = ZCAN_DEVICE_TYPE(40)
ZCAN_USBCANFD_200U    = ZCAN_DEVICE_TYPE(41)
ZCAN_USBCANFD_100U    = ZCAN_DEVICE_TYPE(42)
ZCAN_USBCANFD_MINI    = ZCAN_DEVICE_TYPE(43)
ZCAN_CANFDCOM_100IE   = ZCAN_DEVICE_TYPE(44)
ZCAN_CANSCOPE         = ZCAN_DEVICE_TYPE(45)
ZCAN_CLOUD            = ZCAN_DEVICE_TYPE(46)
ZCAN_CANDTU_NET_400   = ZCAN_DEVICE_TYPE(47)
ZCAN_VIRTUAL_DEVICE   = ZCAN_DEVICE_TYPE(99)

'''
接口返回状态
'''
ZCAN_STATUS_ERR         = 0
ZCAN_STATUS_OK          = 1
ZCAN_STATUS_ONLINE      = 2
ZCAN_STATUS_OFFLINE     = 3
ZCAN_STATUS_UNSUPPORTED = 4

'''
can 类型
'''
ZCAN_TYPE_CAN    = c_uint(0)
ZCAN_TYPE_CANFD  = c_uint(1)

'''
设备信息
'''
class ZCAN_DEVICE_INFO(Structure):
    # 设备信息
    _fields_ = [("hw_Version", c_ushort),
                ("fw_Version", c_ushort),
                ("dr_Version", c_ushort), 
                ("in_Version", c_ushort), 
                ("irq_Num", c_ushort),
                ("can_Num", c_ubyte),
                ("str_Serial_Num", c_ubyte * 20),
                ("str_hw_Type", c_ubyte * 40),
                ("reserved", c_ushort * 4)]

    def __str__(self):
        return f"""Hardware Version: {self.hw_version}
Firmware Version: {self.fw_version}
Driver Interface: {self.dr_version}
Interface Interface: {self.in_version}
Interrupt Number: {self.irq_num}
CAN Number: {self.can_num}
Serial: {self.serial}
Hardware Type: {self.hw_type}
        """
                
    def _version(self, version):
        return ("V%02x.%02x" if version // 0xFF >= 9 else "V%d.%02x") % (version // 0xFF, version & 0xFF)
    
    @property
    def hw_version(self):
        # 硬件版本号， 16 进制， 比如 0x0100 表示 V1.00。
        return self._version(self.hw_Version)

    @property
    def fw_version(self):
        # 固件版本号， 16 进制
        return self._version(self.fw_Version)
    
    @property
    def dr_version(self):
        # 驱动程序版本号， 16 进制。
        return self._version(self.dr_Version)
    
    @property
    def in_version(self):
        # 接口库版本号， 16 进制。
        return self._version(self.in_Version)

    @property
    def irq_num(self):
        # 板卡所使用的中断号。
        return self.irq_Num

    @property
    def can_num(self):
        # 表示有几路通道。
        return self.can_Num

    @property
    def serial(self):
        # 此板卡的序列号，比如” USBCAN V1.00”（注意：包括字符串结束符’\0’）
        serial = ''
        for c in self.str_Serial_Num:
            if c > 0: 
                serial += chr(c)
            else:
                break 
        return serial

    @property
    def hw_type(self):
        # 硬件类型。
        hw_type = ''
        for c in self.str_hw_Type:
            if c > 0:
                hw_type += chr(c)
            else:
                break
        return hw_type

class _ZCAN_CHANNEL_CAN_INIT_CONFIG(Structure):
    # 定义了初始化配置的参数， 调用 ZCAN_InitCAN 之前，要先初始化该结构体
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("reserved", c_uint),
                ("filter",   c_ubyte),
                ("timing0",  c_ubyte),
                ("timing1",  c_ubyte),
                ("mode",     c_ubyte)]

class _ZCAN_CHANNEL_CANFD_INIT_CONFIG(Structure):
    _fields_ = [("acc_code",     c_uint),
                ("acc_mask",     c_uint),
                ("abit_timing",  c_uint),
                ("dbit_timing",  c_uint),
                ("brp",          c_uint),
                ("filter",       c_ubyte),
                ("mode",         c_ubyte),
                ("pad",          c_ushort),
                ("reserved",     c_uint)]

class _ZCAN_CHANNEL_INIT_CONFIG(Union):
    _fields_ = [("can", _ZCAN_CHANNEL_CAN_INIT_CONFIG), ("canfd", _ZCAN_CHANNEL_CANFD_INIT_CONFIG)]

class ZCAN_CHANNEL_INIT_CONFIG(Structure):
    # 定义了初始化配置的参数， 调用 ZCAN_InitCAN 之前，要先初始化该结构体。
    _fields_ = [("can_type", c_uint),
                ("config", _ZCAN_CHANNEL_INIT_CONFIG)]

class ZCAN_CHANNEL_ERR_INFO(Structure):
    # 包含总线错误信息， 在函数 ZCAN_ReadChannelErrInfo 中被填充。
    _fields_ = [("error_code", c_uint),
                ("passive_ErrData", c_ubyte * 3),
                ("arLost_ErrData", c_ubyte)]

class ZCAN_CHANNEL_STATUS(Structure):
    # 包含控制器状态信息，在函数 ZCAN_ReadChannelStatus中被填充
    _fields_ = [("errInterrupt", c_ubyte),
                ("regMode",      c_ubyte),
                ("regStatus",    c_ubyte), 
                ("regALCapture", c_ubyte),
                ("regECCapture", c_ubyte),
                ("regEWLimit",   c_ubyte),
                ("regRECounter", c_ubyte),
                ("regTECounter", c_ubyte),
                ("Reserved",     c_uint)]

class ZCAN_CAN_FRAME(Structure):
    # can 报文
    _fields_ = [("can_id",  c_uint, 29),
                ("err",     c_uint, 1),
                ("rtr",     c_uint, 1),
                ("eff",     c_uint, 1), 
                ("can_dlc", c_ubyte),
                ("__pad",   c_ubyte),
                ("__res0",  c_ubyte),
                ("__res1",  c_ubyte),
                ("data",    c_ubyte * 8)]

class ZCAN_CANFD_FRAME(Structure):
    # canfd 报文
    _fields_ = [("can_id", c_uint, 29), 
                ("err",    c_uint, 1),
                ("rtr",    c_uint, 1),
                ("eff",    c_uint, 1), 
                ("len",    c_ubyte),
                ("brs",    c_ubyte, 1),
                ("esi",    c_ubyte, 1),
                ("__res",  c_ubyte, 6),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data",   c_ubyte * 64)]


class ZCANdataFlag(Structure):
    # flag 字段表示 CAN/CANFD 帧的标记信息，长度 4 字节
    _fields_= [("frameType", c_uint, 2),  # 帧类型 0： CAN 帧 1： CANFD 帧
                ("txDelay", c_uint, 2), # 队列发送延时， 延时时间存放在 timeStamp 字段
                                        # 0： 不启用延时19
                                        # 1： 启用延时，延时时间单位为 1 毫秒(1ms)
                                        # 2： 启用延时，延时时间单位为 100 微秒(0.1ms)
                                        # 队列发送延时支持情况取决于设备，具体见对应设备的队列发送章节。
                ("transmitType", c_uint, 4),
                                        # 队列发送延时， 延时时间存放在 timeStamp 字段
                                        # 0： 不启用延时19
                                        # 1： 启用延时，延时时间单位为 1 毫秒(1ms)
                                        # 2： 启用延时，延时时间单位为 100 微秒(0.1ms)
                                        # 队列发送延时支持情况取决于设备，具体见对应设备的队列发送章节。
                ("txEchoRequest", c_uint, 1),
                                        # 发送回显请求
                                        # 0：不需要设备回显发送帧
                                        # 1：请求设备回显发送帧
                                        # 支持发送回显的设备，发送数据时将此位置 1，设备可以通过接收数据接口收到发送出去的数据帧，
                                        # 接收到的发送数据使用 txEchoed 位标记通过发送回显请求，可以获取帧发送到总线的准确时间
                ("txEchoed", c_uint, 1),
                                        # 报文是否是发送回显报文
                                        # 0：正常总线接收到的报文
                                        # 1：本设备发送回显报文
            ("reserved", c_uint, 22),  # 保留字段
            ]



class ZCANFDData(Structure):            
    # 表示 CAN/CANFD 帧结构,目前仅作为 ZCANDataObj 结构的成员使用
    _fields_= [("timestamp",c_uint64),         # 时间戳， 作为接收帧时， 时间戳单位微秒(us)。正常发送时， timeStamp 字段无意义
               ("flag",ZCANdataFlag),       # flag 字段表示 CAN/CANFD 帧的标记信息，长度 4 字节
               ("extraData",c_ubyte*4),     # 帧附加数据，暂未使用
               ("frame",ZCAN_CANFD_FRAME),
                                            # frame 成员用于保存 CAN/CANFD 帧的 ID， 标准帧/扩展帧，数据帧/远程帧标记，帧长度， 
                                            # 数据等信息。 
                                            # frame 结构可以参考 canfd_frame 结构体说明部分。
               ]





class ZCANDataObj(Structure):
    # 该结构作为合并接收使用的各种数据的载体，支持 CAN，CANFD， LIN， GPS，错误数据等各种不同类型的数据
    _pack_ =1
    _fields_= [("dataType",c_ubyte),  # ZCAN_DT_ZCAN_CAN_CANFD_DATA
               ("chnl",c_ubyte),  # 通道
               ("flag",c_ushort),  # 数据标志，暂未使用
               ("extraData",c_ubyte*4),  # 额外数据，暂未使用
               ("zcanfddata",ZCANFDData),  ##88个字节
               ("reserve",c_ubyte*4)
               ]





class ZCAN_Transmit_Data(Structure):
    # 包含发送的 CAN 报文信息， 在函数 ZCAN_Transmit 中使用
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("transmit_type", c_uint)]

class ZCAN_Receive_Data(Structure):
    #  包含接收的 CAN 报文信息， 在函数 ZCAN_Receive 中使用
    _fields_  = [("frame", ZCAN_CAN_FRAME), ("timestamp", c_ulonglong)]

class ZCAN_TransmitFD_Data(Structure):
    # 包含发送的 CANFD 报文信息，在函数 ZCAN_TransmitFD中使用
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("transmit_type", c_uint)]


class ZCAN_ReceiveFD_Data(Structure):
    # 包含接收的 CANFD 报文信息， 在函数 ZCAN_ReceiveFD中使用
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("timestamp", c_ulonglong)]

class ZCAN_AUTO_TRANSMIT_OBJ(Structure):
    # 包含定时发送CAN参数信息
    _fields_ = [("enable",   c_ushort),  # 使能本条报文, 0: 禁能, 1: 使能
                ("index",    c_ushort),  # 报文编号, 从0开始，报文相同则使用最新的一条消息。
                ("interval", c_uint),  # 发送周期，单位毫秒
                ("obj",      ZCAN_Transmit_Data)]  # 发送的报文， 详见 ZCAN_Transmit_Data 结构说明

class ZCANFD_AUTO_TRANSMIT_OBJ(Structure):
    # 包含定时发送的CANfd参数信息
    _fields_ = [("enable",   c_ushort),
                ("index",    c_ushort),
                ("interval", c_uint),
                ("obj",      ZCAN_TransmitFD_Data)]  # 发送的报文， 详见 ZCAN_TransmitFD_Data 结构说明。

class ZCANFD_AUTO_TRANSMIT_OBJ_PARAM(Structure):   #auto_send delay
    # 用于设置定时发送额外的参数，目前只支持 USBCANFD-X00U 系列设备
    _fields_ = [("indix",  c_ushort),
                ("type",   c_ushort),
                ("value",  c_uint)]

class IProperty(Structure):
    # # 用于获取/设置设备参数信息
    _fields_ = [("SetValue", c_void_p), 
                ("GetValue", c_void_p),
                ("GetPropertys", c_void_p)]

class ZCAN(object):


    def __init__(self):
        self.__log = Logs()
        self.__dll = load_vendor_library("zlgcan_x64", "zlgcan.dll")
        configure_zcan(self.__dll)

    def close_log(self):
        self.__log.close()

    @FunctionRely.try_func_comments("Exception on OpenDevice!")
    def OpenDevice(self, device_type, device_index, reserved):
        """
        该函数用于打开设备。 一个设备只能被打开一次
        :param  device_type: 设备类型
        :param  device_index: 设备索引号
        :param  reserved: 仅作保留
        :return: 为 INVALID_DEVICE_HANDLE 0 表示操作失败，否则表示操作成功，返回设备句柄值
        """
        ret = self.__dll.ZCAN_OpenDevice(device_type, device_index, reserved)
        self.__log.debug(f"打开{device_type}类型的周立功设备，"
                        f"当前电脑的第{device_index+1}个周立功设备。"
                        f"当前设备的句柄为:{ret}")
        return ret


    @FunctionRely.try_func_comments("Exception on CloseDevice!")
    def CloseDevice(self, device_handle):
        """
        该函数用于关闭设备，关闭设备和打开设备一一对应。
        :param  device_handle: 需要关闭的设备的句柄值， 即 ZCAN_OpenDevice 成功返回的值。
        :return: STATUS_OK 1 表示操作成功， STATUS_ERR 0 表示操作失败
        """
        ret = self.__dll.ZCAN_CloseDevice(device_handle)
        self.__log.debug(f"关闭设备，操作结果{ret}")
        return ret
        

    @FunctionRely.try_func_comments("Exception on ZCAN_GetDeviceInf")
    def GetDeviceInf(self, device_handle):
        """
        该函数用于获取设备信息
        :param  device_handle: 设备句柄值
        :return: info 设备信息结构体， 详见 ZCAN_DEVICE_INFO 结构说明
        """
        info = ZCAN_DEVICE_INFO()
        ret = self.__dll.ZCAN_GetDeviceInf(device_handle, byref(info))
        self.__log.debug(f"设备{device_handle}信息:\n{info}操作结果{ret}")
        return info if ret == ZCAN_STATUS_OK else None
        

    @FunctionRely.try_func_comments("Exception on ZCAN_ZCAN_IsDeviceOnLine!")
    def DeviceOnLine(self, device_handle):
        """
        该函数用于检测设备是否在线， 仅支持 USB 系列设备。
        :param  device_handle: 设备句柄值
        :return: 设备在线=STATUS_ONLINE 2  不在线=STATUS_OFFLINE 3
        """
        ret = self.__dll.ZCAN_IsDeviceOnLine(device_handle)
        self.__log.debug(f"设备{device_handle}，在线状态{ret}")
        return ret


    @FunctionRely.try_func_comments("Exception on ZCAN_InitCAN!")
    def InitCAN(self, device_handle, can_index):
        """
        该函数用于初始化 CAN。
        :param  device_handle: 设备句柄值
        :param  can_index: 通道索引号， 通道 0 的索引号为 0， 通道 1 的索引号为 1， 以此类推。
        :return: 为 INVALID_CHANNEL_HANDLE 0 表示操作失败， 否则表示操作成功， 返回通道句柄值
        """
        init_config: ZCAN_CHANNEL_INIT_CONFIG = ZCAN_CHANNEL_INIT_CONFIG()
        init_config.can_type = ZCAN_TYPE_CANFD
        init_config.config.canfd.mode = 0
        ret = self.__dll.ZCAN_InitCAN(device_handle, can_index, byref(init_config))
        self.__log.debug(f"初始化设备{device_handle}通道{can_index} -> {ret}")
        return ret
        

    @FunctionRely.try_func_comments("Exception on ZCAN_StartCAN!")
    def StartCAN(self, chn_handle):
        """
        该函数用于启动 CAN 通道
        :param  chn_handle: 通道句柄值
        :return:  STATUS_OK 1 表示操作成功， STATUS_ERR 0 表示操作失败
        """
        ret = self.__dll.ZCAN_StartCAN(chn_handle)
        self.__log.debug(f"启动通道{chn_handle}，结果：{ret}")
        return ret
    

    @FunctionRely.try_func_comments("Exception on ZCAN_ResetCAN!")
    def ResetCAN(self, chn_handle):
        """
        该函数用于复位 CAN 通道， 可通过 ZCAN_StartCAN 恢复
        :param  chn_handle: 通道句柄值
        :return: STATUS_OK 1 表示操作成功， STATUS_ERR 0 表示操作失败
        """
        ret = self.__dll.ZCAN_ResetCAN(chn_handle)
        self.__log.debug(f"复位通道{chn_handle}，结果：{ret}")
        return ret
        

    @FunctionRely.try_func_comments("Exception on ZCAN_ClearBuffer!")
    def ClearBuffer(self, chn_handle):
        """
        该函数用于清除库接收缓冲区。
        :param  chn_handle:通道句柄值。
        :return: STATUS_OK 表示操作成功， STATUS_ERR 表示操作失败。
        """
        ret = self.__dll.ZCAN_ClearBuffer(chn_handle)
        self.__log.debug(f"清除通道{chn_handle}缓存区{ret}")
        return ret

        
    @FunctionRely.try_func_comments("Exception on ZCAN_ReadChannelErrInfo!")
    def ReadChannelErrInfo(self, chn_handle):
        """
        该函数用于读取通道的错误信息
        :param  chn_handle:通道句柄值。
        :return: ErrInfo 错误信息结构， 详见 ZCAN_CHANNEL_ERROR_INFO 结构说明
        """
        ErrInfo = ZCAN_CHANNEL_ERR_INFO()
        ret = self.__dll.ZCAN_ReadChannelErrInfo(chn_handle, byref(ErrInfo))
        self.__log.debug(f"查询通道{chn_handle}错误信息{ret}")
        return ErrInfo if ret == ZCAN_STATUS_OK else None

        
    @FunctionRely.try_func_comments("Exception on ZCAN_ReadChannelStatus!")
    def ReadChannelStatus(self, chn_handle):
        """
        该函数用于读取通道的状态信息。 目前暂时没有设备支持使用此接口获取通道的状态信息，后续接口可能会被废弃。
        :param  chn_handle:通道句柄值。
        :return: status 状态信息结构， 详见 ZCAN_CHANNEL_STATUS 结构说明
        """
        status = ZCAN_CHANNEL_STATUS()
        ret = self.__dll.ZCAN_ReadChannelStatus(chn_handle, byref(status))
        self.__log.debug(f"查询通道{chn_handle}状态信息{ret}")
        return status if ret == ZCAN_STATUS_OK else None
        
    @FunctionRely.try_func_comments("Exception on ZCAN_GetReceiveNum!")
    def GetReceiveNum(self, chn_handle, can_type = ZCAN_TYPE_CAN):
        """
        获取缓冲区中 CAN， CANFD 或者合并接收报文数目
        :param  chn_handle: 通道句柄值
        :param  can_type: 获取 CAN， CANFD 或者合并接收报文， 0=CAN， 1=CANFD， 2=合并接收。
        :return: 报文数目
        """
        self.__log.debug(f"获取通道{chn_handle}缓存区{can_type}报文数目")
        return self.__dll.ZCAN_GetReceiveNum(chn_handle, can_type)
    
    @FunctionRely.try_func_comments("Exception on ZCAN_Transmit!")
    def Transmit(self, chn_handle, std_msg, len):
        """
        该函数用于发送 CAN 报文
        :param  chn_handle: 通道句柄值
        :param  std_msg: 结构体 ZCAN_Transmit_Data 数组的首指针
        :param  len: 报文数目
        :return: 实际发送的报文数目
        """
        return self.__dll.ZCAN_Transmit(chn_handle, std_msg, len)
        
    @FunctionRely.try_func_comments("Exception on ZCAN_Receive!")
    def Receive(self, chn_handle, rcv_num, wait_time = c_int(-1)):
        """
        该函数用于接收CAN报文， 建议使用ZCAN_GetReceiveNum确保缓冲区有数据再使用
        :param  chn_handle: 通道句柄值
        :param  rcv_num: 数组长度（本次接收的最大报文数目， 实际返回值小于等于这个值） 
        :param  wait_time: 缓冲区无数据，函数阻塞等待时间，单位毫秒。 若为-1 则表示无超时，一直等待，默认值为-1。
        :return: rcv_can_msgs: 结构体 ZCAN_Receive_Data   ret: 实际接收的报文数目
        """
        rcv_can_msgs = (ZCAN_Receive_Data * rcv_num)()
        ret = self.__dll.ZCAN_Receive(chn_handle, byref(rcv_can_msgs), rcv_num, wait_time)
        return rcv_can_msgs, ret
        
    @FunctionRely.try_func_comments("Exception on ZCAN_TransmitFD!")
    def TransmitFD(self, chn_handle, fd_msg, len):
        """
        该函数用于发送 CANFD 报文
        :param  chn_handle: 通道句柄值
        :param  fd_msg: 结构体 ZCAN_TransmitFD_Data 数组的首指针
        :param  len: 报文数目
        :return: 实际发送成功的报文数目
        """
        return self.__dll.ZCAN_TransmitFD(chn_handle, byref(fd_msg), len)
            
            
    @FunctionRely.try_func_comments("Exception on ZCAN_TransmitData!")
    def TransmitData(self, device_handle, msg, len, timing=None):
        """
        该函数用于发送 CAN/CANFD 报文。
        :param  device_handle: 设备句柄
        :param  msg: 结构体 ZCANDataObj 数组的首指针。
        :param  len: 报文数目
        :return: 实际发送的报文数目
        """
        transmit = self.__dll.ZCAN_TransmitData
        pointer = byref(msg)
        if timing is None:
            return transmit(device_handle, pointer, len)
        timing.api_start_ns = time.perf_counter_ns()
        try:
            timing.returned_frames = transmit(device_handle, pointer, len)
        finally:
            timing.api_return_ns = time.perf_counter_ns()
        return timing.returned_frames

            
    @FunctionRely.try_func_comments("Exception on ZCAN_ReceiveFD!")
    def ReceiveFD(self, chn_handle, rcv_num, wait_time = c_int(-1)):
        """
        该函数用于接收 CANFD 数据，建议使用 ZCAN_GetReceiveNum 确保缓冲区有数据再使用
        :param  chn_handle: 通道句柄值
        :param  rcv_num: 数组长度（本次接收的最大报文数目， 实际返回值小于等于这个值）
        :param  wait_time: 缓冲区无数据，函数阻塞等待时间，单位毫秒。 若为-1 则表示无超时，一直等待，默认值为-1。
        :return: rcv_canfd_msgs: 结构体 ZCAN_ReceiveFD_Data 数组, ret: 实际接收的报文数目
        """
        rcv_canfd_msgs = (ZCAN_ReceiveFD_Data * rcv_num)()
        ret = self.__dll.ZCAN_ReceiveFD(chn_handle, byref(rcv_canfd_msgs), rcv_num, wait_time)
        return rcv_canfd_msgs, ret
        
    @FunctionRely.try_func_comments("Exception on ZCAN_ReceiveData!")
    def ReceiveData(self, device_handle, rcv_num, wait_time = c_int(-1)):
        """
        该函数用于接收 CAN、 CANFD、 LIN、 GPS、错误数据等各种类型的数据，即合并接收功能。 
        建议使用 ZCAN_GetReceiveNum 接口使用合并接收参数获取缓冲区合并接收帧数量后确保缓冲区有数据再调用接口获取数据
        :param  device_handle: 设备句柄值
        :param  rcv_num: 数组长度
        :param  wait_time: 缓冲区无数据，函数阻塞等待时间，单位毫秒。 若为-1 则表示无超时，一直等待，默认值为-1。
        :return: rcv_can_data_msgs: 结构体 ZCANDataObj 数组,ret: 实际接收的报文数目
        """
        rcv_can_data_msgs = (ZCANDataObj * rcv_num)()
        ret = self.__dll.ZCAN_ReceiveData(device_handle , byref(rcv_can_data_msgs),rcv_num,wait_time)
        return  rcv_can_data_msgs ,ret
        

    @FunctionRely.try_func_comments("Exception on ZCAN_GetIProperty!")
    def GetIProperty(self, device_handle):
        """
        该函数返回属性配置接口
        :param  device_handle: 设备句柄值
        :return: 返回属性配置接口指针， 详见 IProperty 结构说明， 空则表示操作失败。
        """
        self.__dll.GetIProperty.restype = POINTER(IProperty)
        ret = self.__dll.GetIProperty(device_handle)
        self.__log.debug(f"获取{device_handle}设备属性接口：{ret}")
        return ret
        
    @FunctionRely.try_func_comments("Exception on IProperty SetValue")
    def SetValue(self, iproperty, path, value):
        """
        CAN 接口卡的属性配置
        :param  iproperty: 配置接口
        :param  path: 设备属性路径。
        :param  value: 要设置的属性值
        :return: STATUS_OK 表示设置成功， STATUS_ERR 表示设置失败
        """
        func = CFUNCTYPE(c_uint, c_char_p, c_char_p)(iproperty.contents.SetValue)
        ret = func(c_char_p(path.encode("utf-8")), c_char_p(value.encode("utf-8")))
        if ret != ZCAN_STATUS_OK:
            self.__log.error("Set %s failed!" %(path))
        return ret
        

    @FunctionRely.try_func_comments("Exception on IProperty SetValue1")
    def SetValue1(self, iproperty, path, value): 
        """
        CAN 接口卡的属性配置
        :param  iproperty: 配置接口
        :param  path: 设备属性路径。
        :param  value: 要设置的属性值
        :return: STATUS_OK 表示设置成功， STATUS_ERR 表示设置失败
        """
        func = CFUNCTYPE(c_uint, c_char_p, c_char_p)(iproperty.contents.SetValue)
        ret = func(c_char_p(path.encode("utf-8")), c_void_p(value))
        if ret != ZCAN_STATUS_OK:
            self.__log.error("Set %s failed!" %(path))
        return ret                                          
        
            
    @FunctionRely.try_func_comments("Exception on IProperty GetValue")
    def GetValue(self, iproperty, path):
        """
        CAN 接口卡的属性查询
        :param  iproperty: 配置接口
        :param  path: 设备属性路径。
        :return: 返回的指针值非空表示操作成功， 为空(NULL)则表示操作失败
        """
        func = CFUNCTYPE(c_char_p, c_char_p)(iproperty.contents.GetValue)
        return func(c_char_p(path.encode("utf-8")))

    @FunctionRely.try_func_comments("Exception on ZCAN_ReleaseIProperty!")
    def ReleaseIProperty(self, iproperty):
        """
        释放属性接口， 与 GetIProperty 结对使用
        :param  iproperty: 配置接口
        :return: STATUS_OK 表示操作成功， STATUS_ERR 表示操作失败
        """
        return self.__dll.ReleaseIProperty(iproperty)
        


###############################################################################
class DBC():

    def __init__(self, dbc_path: str):
        raw = Path(dbc_path).read_bytes()
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            break
        else:
            raise ValueError("无法读取 DBC 文本，请使用 UTF-8 或 GB18030 编码。")
        self.db = cantools.db.load_string(text, database_format="dbc", strict=False)
        self.__log = Logs()


    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.__log.close()

    def get_message_by_id(self, message_id: str) -> Message:
        """
        根据报文id获取当前dbc的报问
        :param message_id: 报文id
        :return: 报文
        """
        message = self.db.get_message_by_frame_id(int(message_id, 16))
        self.__log.debug(f"message信息：{message}")

        return message


    def get_message_by_name(self, message_name: str) -> Message:
        """
        根据报文名称获取当前dbc的报文frame_id
        :param message_name: 报文名称
        :return: 报文
        """
        message = self.db.get_message_by_name(message_name)
        self.__log.debug(f"message信息：{message}")

        return message


    def get_single_value(self, select_data: dict, value_type_is_num: bool = False) -> dict:
        """
        根据包含有message_id的字典，查询当前报文的信号表示的含义
        :param select_data: 要查询的信息，包含 message_id 和 data {message_id: id,data: data}
        :param value_type_is_num: 查询结果为数字还是值
        :return: 报文详情
        """

        # 在dbc中找到该信号供解析使用
        select_message = self.db.get_message_by_frame_id(int(select_data['message_id'], 16))

        message_key: bytes = b""
        for i in select_data['data']:
            message_key += struct.pack('B', i)

        if value_type_is_num:

            result = select_message.decode(message_key,
                                        decode_choices=False,  # 值为数字为 False 值为 True
                                        scaling=True,
                                        decode_containers=False)
        else:
            result = select_message.decode(message_key,
                                        decode_choices=True,  # 值为数字为 False 值为 True
                                        scaling=True,
                                        decode_containers=False)

        self.__log.debug(f"报文{select_data['message_id']}值为：{result}")

        return result

    def get_single_array(self, name: str,single_data: dict = {})-> tuple:
        """
        根据报文名称和详情，返回字节形式的报文
        :param name: 要查询的报文名称
        :param single_data: 报文详情
        :return: 报文详情
        """
        # 查询报文对象
        message = self.db.get_message_by_name(name)
        # 将dbc中所有的键值对取出来
        data_orig = {}
        for i in range(len(message.signals)):
            signal = message.signals[i]
            data_orig[signal.name] = 0
        # 更新该字典键值对的值
        data_orig.update(single_data)
        # 返回字节形式
        msgdata = message.encode(data_orig, scaling=True, padding=False, strict=False)
        data_final = self.single_format(str(binascii.b2a_hex(msgdata), 'utf-8'))

        return message, data_final

    def single_format(self, single: str) -> str:
        """
        格式化信号
        :param single: 信号
        :return: 增加空格的信号
        """
        single_length: int = len(single)
        result: str = ""
        for i in range(single_length):
            if i > 0 and i%2 ==0 and i < single_length:
                result += " "+ single[i]
            else:
                result += single[i]

        return result
    
    def message_list_get(self)-> list:
        """
        返回当前dbc报文列表
        :return: 
        """
        return self.db.messages

###############################################################################
_WINDOWS_ERROR_DATA = struct.Struct("<Q6B2x")
_WINDOWS_ERROR_NAMES = {
    (1, 2): "ACK 错误",
    (2, 1): "控制器接收 FIFO 溢出",
    (2, 2): "驱动接收缓冲区溢出",
    (2, 3): "驱动发送缓冲区溢出",
    (3, 1): "设备接收缓冲区溢出",
    (3, 2): "设备发送缓冲区溢出",
}


class WindowsCanBackend:
    """Keep device handles, channel handles and ZCAN structures at the SDK boundary."""

    supports_diagnostics = True
    continuous_rx = True
    receive_interval = 0.05

    def __init__(self, device_index):
        self._sdk = ZCAN()
        self._channels = {}
        self._handle = self._sdk.OpenDevice(ZCAN_USBCANFD_200U, device_index, 0)
        if not self._handle:
            self._sdk.close_log()
            raise RuntimeError("打开设备失败，请检查周立功驱动、USB 连接及设备占用情况。")
        self._epoch = time.time()
        self._tx_echo = os.environ.get("ZCAN_WINDOWS_TX_ECHO", "1") != "0"
        self._error_log = Logs()
        self._device_error_counts = Counter()
        self._device_error_samples = {}
        self._last_device_error_log = float("-inf")

    @property
    def channels(self):
        return tuple(sorted(self._channels))

    def open_channel(self, channel):
        validate_channel(channel)
        if channel in self._channels:
            return
        if not self._handle:
            raise RuntimeError("设备已关闭。")

        self._sdk.GetDeviceInf(self._handle)
        properties = self._sdk.GetIProperty(self._handle)
        if not properties:
            raise RuntimeError("无法读取设备属性接口。")
        try:
            for name, value in (
                ("clock", "60000000"),
                ("canfd_standard", "0"),
                ("initenal_resistance", "1"),
                ("canfd_abit_baud_rate", "500000"),
                ("canfd_dbit_baud_rate", "2000000"),
                ("set_device_recv_merge", "1"),
            ):
                self._sdk.SetValue(properties, f"{channel}/{name}", value)
        finally:
            self._sdk.ReleaseIProperty(properties)

        handle = self._sdk.InitCAN(self._handle, channel)
        if not handle:
            raise RuntimeError(f"初始化通道 {channel} 失败。")
        self._sdk.ClearBuffer(handle)
        if self._sdk.StartCAN(handle) != ZCAN_STATUS_OK:
            self._sdk.ResetCAN(handle)
            raise RuntimeError(f"启动通道 {channel} 失败。")
        self._channels[channel] = handle

    def close_channel(self, channel):
        if channel in self._channels:
            if self._sdk.ResetCAN(self._channels[channel]) != ZCAN_STATUS_OK:
                raise RuntimeError(f"关闭通道 {channel} 失败。")
            del self._channels[channel]

    tx_api_name = "ZCAN_TransmitData"

    def send_timed(self, message, timing):
        self.send(message, timing)

    def send(self, message, timing=None):
        if message.channel not in self._channels:
            raise RuntimeError(f"通道 {message.channel} 未打开。")
        items = (ZCANDataObj * 1)()
        item = items[0]
        item.dataType = 1
        item.chnl = message.channel
        data = item.zcanfddata
        data.flag.frameType = int(message.is_fd)
        data.flag.txEchoRequest = int(self._tx_echo)
        frame = data.frame
        frame.can_id = message.arbitration_id
        frame.eff = int(message.is_extended_id)
        frame.rtr = int(message.is_remote_frame)
        frame.len = message.dlc
        frame.brs = int(message.bitrate_switch)
        frame.data[:len(message.data)] = message.data
        if timing is None:
            count = self._sdk.TransmitData(self._handle, items, 1)
        else:
            count = self._sdk.TransmitData(self._handle, items, 1, timing)
        if count != 1:
            raise RuntimeError(f"通道 {message.channel} 发送失败。")

    def poll_raw(self, limit):
        count = max((self._sdk.GetReceiveNum(handle, 2) or 0
                     for handle in self._channels.values()), default=0)
        if not count:
            return None
        batch = self._sdk.ReceiveData(self._handle, min(count, limit), 0)
        if batch is None:
            raise RuntimeError("读取设备接收队列失败。")
        return batch

    def poll(self, limit):
        return self.decode_poll(self.poll_raw(limit))

    def decode_poll(self, batch):
        self._flush_device_errors()
        if batch is None:
            return []
        items, received = batch
        frames = []
        for item in items[:min(received, len(items))]:
            if item.dataType == 2:
                self._record_device_error(item)
                continue
            data = item.zcanfddata
            frame = data.frame
            is_fd = data.flag.frameType == 1
            if item.dataType != 1 or frame.len > (64 if is_fd else 8):
                continue
            frames.append(can_Message(
                timestamp=self._epoch + data.timestamp / 1_000_000,
                channel=item.chnl, arbitration_id=frame.can_id,
                is_extended_id=bool(frame.eff), is_remote_frame=bool(frame.rtr),
                is_error_frame=bool(frame.err), is_fd=is_fd,
                bitrate_switch=bool(frame.brs), error_state_indicator=bool(frame.esi),
                is_rx=not data.flag.txEchoed, dlc=frame.len,
                data=bytes(frame.data[:frame.len]) if not frame.rtr else b"",
            ))
        self._flush_device_errors()
        return frames

    def _record_device_error(self, item):
        stamp, kind, subtype, state, rx, tx, detail = _WINDOWS_ERROR_DATA.unpack_from(
            bytes(item), ZCANDataObj.zcanfddata.offset)
        label = _WINDOWS_ERROR_NAMES.get((kind, subtype), "设备错误")
        if state == 4:
            label = f"Bus-Off / {label}"
        key = (item.chnl if item.chnl in (0, 1) else -1, label)
        self._device_error_counts[key] += 1
        self._device_error_samples[key] = (item.chnl, stamp, kind, subtype, state, rx, tx, detail)

    def _flush_device_errors(self, *, force=False):
        now = time.monotonic()
        if not force and now - self._last_device_error_log < 1:
            return
        if not self._device_error_counts:
            return
        for key, count in self._device_error_counts.items():
            _, label = key
            channel, stamp, kind, subtype, state, rx, tx, detail = self._device_error_samples[key]
            self._error_log.warning(
                f"Windows CAN 通道 {channel} {label} ×{count}，末次设备时间={stamp}us，"
                f"类型={kind}/{subtype}，状态={state}，RX={rx}，TX={tx}，详情={detail:#04x}。")
        self._last_device_error_log = now
        self._device_error_counts.clear()
        self._device_error_samples.clear()

    def clear(self, channel):
        if channel not in self._channels:
            raise RuntimeError(f"通道 {channel} 未打开。")
        if self._sdk.ClearBuffer(self._channels[channel]) != ZCAN_STATUS_OK:
            raise RuntimeError(f"清除通道 {channel} 缓冲区失败。")

    def is_online(self):
        return bool(self._handle and self._sdk.DeviceOnLine(self._handle) == ZCAN_STATUS_ONLINE)

    def close(self):
        if self._handle:
            if self._sdk.CloseDevice(self._handle) != ZCAN_STATUS_OK:
                raise RuntimeError("关闭设备失败。")
            self._handle = None
        self._channels.clear()
        self._sdk.close_log()
        self._flush_device_errors(force=True)
        self._error_log.close()


def backend_mode() -> str:
    mode = os.environ.get("ZCAN_BACKEND", "").strip().lower() or "hardware"
    if mode == "hardware" and platform.system() == "Darwin":
        return "mac-usb"
    return mode


def open_backend(device_index: int) -> CanBackend:
    mode = backend_mode()
    if mode == "simulation":
        return LoopbackBackend()
    if mode == "mac-usb":
        from zlg.macos_usb import MacUsbCanBackend

        return MacUsbCanBackend()
    if mode == "hardware":
        return WindowsCanBackend(device_index)
    raise ValueError("ZCAN_BACKEND 必须为 hardware、simulation 或 mac-usb。")


class zlgCan:
    """DBC, scheduling and logging shared by all CAN backends."""

    def __init__(self, device_index: int, dbc_path: str, *, hardware_periodic=None):
        self.db = DBC(dbc_path)
        self.__log = Logs()
        try:
            self._backend = open_backend(device_index)
            supports_hardware_periodic = getattr(self._backend, "supports_hardware_periodic", False)
            if hardware_periodic is None:
                hardware_periodic = supports_hardware_periodic
            if hardware_periodic and not supports_hardware_periodic:
                self._backend.close()
                raise RuntimeError("当前设备后端未接入硬件定时发送。")
        except Exception:
            self.db.close()
            self.__log.close()
            raise
        self._state_lock = threading.RLock()
        self._timed_send = getattr(self._backend, "send_timed", None)
        self._tx_timing = (TxTiming(getattr(self._backend, "tx_api_name", "backend.send"))
                           if os.environ.get("ZCAN_TX_TIMING") == "1" else None)
        self._io_lock = threading.Lock()
        self._rx_lock = threading.Lock()
        self._poll_raw = getattr(self._backend, "poll_raw", None)
        self._closed = False
        self.hardware_periodic = hardware_periodic
        self._periodic_faults = set()
        self._open_channels = self._backend.channels
        self._pending = deque(maxlen=4096)
        self._diagnostic_frames = {channel: deque(maxlen=512) for channel in (0, 1)}
        self._continuous_rx = getattr(self._backend, "continuous_rx", False)
        self._receive_stop = threading.Event()
        self._receive_thread = None
        self._last_bus_error_log = float("-inf")
        self.receive_error = None
        self.custom_log = None
        self.thread_messages = {}
        self.__monitor_switch = False

    @property
    def channels(self):
        # Lifecycle methods publish an immutable snapshot. Reading it from the
        # GUI must not wait for an in-flight send.
        return self._open_channels

    @property
    def supports_diagnostics(self):
        return self._backend.supports_diagnostics

    def chn_open(self, chn_index):
        with self._state_lock:
            with self._io_lock:
                self._backend.open_channel(chn_index)
                self._open_channels = self._backend.channels
            self._sync_receiver()

    def reset(self, chn_index):
        validate_channel(chn_index)
        with self._state_lock:
            self.chn_close_setup(chn_index, software_only=True)
            with self._rx_lock:
                if self.custom_log is not None:
                    self._drain_recording_frames()
                with self._io_lock:
                    self._backend.close_channel(chn_index)
                    self._open_channels = self._backend.channels
                self._discard_channel_frames(chn_index)
            self._forget_hardware_tasks(chn_index)
            self._periodic_faults.discard(chn_index)
            self._sync_receiver()

    def close(self):
        with self._state_lock:
            if not self._closed:
                self.chn_close_setup(software_only=True)
                self.blf_stop()
                self._stop_receiver()
                with self._rx_lock:
                    with self._io_lock:
                        try:
                            self._backend.close()
                        finally:
                            self._open_channels = self._backend.channels
                            if not self._open_channels:
                                self._forget_hardware_tasks()
                    self._closed = True
                    self._pending.clear()
                    for frames in self._diagnostic_frames.values():
                        frames.clear()
                if self._tx_timing is not None:
                    try:
                        path = self._tx_timing.export()
                        self.__log.info(f"周期发送统计已保存：{path}")
                    except OSError as exc:
                        self.__log.error(f"无法保存周期发送统计：{exc}")
                self.db.close()
                self.__log.close()
        return 1

    def is_online(self):
        with self._io_lock:
            return int(self._backend.is_online())

    @staticmethod
    def _message(chn_index, message, data_final, can_fd, fd_brs):
        validate_channel(chn_index)
        payload = bytes.fromhex(data_final)
        if can_fd and len(payload) not in (*range(9), 12, 16, 20, 24, 32, 48, 64):
            raise ValueError("CAN FD 帧数据长度无对应 DLC。")
        return can_Message(
            channel=chn_index, arbitration_id=message.frame_id,
            is_extended_id=message.is_extended_frame, data=payload,
            is_fd=can_fd, bitrate_switch=can_fd and fd_brs, is_rx=False, check=True,
        )

    def _send(self, message):
        with self._io_lock:
            self._backend.send(message)

    def message_create(self, chn_index, message, data_final, can_type=1):
        self._send(self._message(chn_index, message, data_final, can_type > 0, can_type == 2))

    def _send_periodic(self, worker, *, first=False):
        entered = time.perf_counter_ns()
        sample = None
        try:
            with self._io_lock:
                acquired = time.perf_counter_ns()
                pending = worker.pending(acquired)
                if pending is None:
                    return
                frame, due, period = pending
                anchor = acquired if first or not worker.submitted else due
                if self._tx_timing is not None:
                    sample = TxSample(worker.task_id, frame.channel, frame.arbitration_id,
                                      frame.is_extended_id, frame.is_fd, period, due, entered, acquired)
                try:
                    if sample is not None and self._timed_send is not None:
                        self._timed_send(frame, sample)
                    elif sample is not None:
                        sample.api_start_ns = time.perf_counter_ns()
                        try:
                            self._backend.send(frame)
                            sample.returned_frames = 1
                        finally:
                            sample.api_return_ns = time.perf_counter_ns()
                    else:
                        self._backend.send(frame)
                    worker.record_submission()
                except (RuntimeError, OSError):
                    if sample is not None:
                        sample.failed = True
                    worker.cancel()
                    raise
                finally:
                    skipped = worker.advance(anchor, time.perf_counter_ns())
                    if sample is not None:
                        sample.skipped_periods = skipped
        finally:
            if sample is not None:
                self._tx_timing.record(sample)

    def _schedule(self, name, signals, frame, period_ns, *, wake=False, background=False, send_count=-1):
        task = self.thread_messages.get((frame.channel, name))
        current = task["func"] if task else None
        if isinstance(current, PulseSend) and current.is_alive():
            raise RuntimeError(f"报文 {name} 正在脉冲发送，请等待完成或先取消。")
        if send_count is None and (current is None or not current.is_alive()
                                   or current.channel != frame.channel):
            raise RuntimeError("这条报文已停止，请重新发送。")
        if self.hardware_periodic and (send_count == -1 or
                (send_count is None and isinstance(current, HardwarePeriodicSend))):
            self._schedule_hardware(name, signals, frame, period_ns // 1_000_000, wake)
            return
        if (isinstance(current, PeriodicSend) and current.is_alive() and current.channel == frame.channel
                and (send_count is None or (send_count == -1 and current.remaining is None))):
            # A periodic send and an update share this lock. Once the update
            # returns, that task cannot submit another frame with the old bytes.
            with self._io_lock:
                if current.update(frame, period_ns):
                    self.thread_messages[(frame.channel, name)] = {**task, "data": signals, "wake": wake}
                    return
        if send_count is None:
            raise RuntimeError("这条报文已停止，请重新发送。")
        if task:
            self._stop_periodic_tasks(((frame.channel, name),))
        worker = PeriodicSend(frame, period_ns, self._send_periodic, self.__log, send_count=send_count)
        if not background:
            self._send_periodic(worker, first=True)
        self.thread_messages[(frame.channel, name)] = {"data": signals, "func": worker, "wake": wake}
        worker.start()

    def _schedule_hardware(self, name, signals, frame, period_ms, wake):
        if not 1 <= period_ms <= self._backend.max_period_ms:
            raise ValueError("硬件定时发送的周期超出范围。")
        current = self.thread_messages.get((frame.channel, name))
        worker = current["func"] if current else None
        if isinstance(worker, HardwarePeriodicSend) and worker.channel == frame.channel:
            slot = worker.slot
        else:
            used = {task["func"].slot for task in self.thread_messages.values()
                    if isinstance(task["func"], HardwarePeriodicSend) and task["func"].channel == frame.channel}
            slot = next((i for i in range(self._backend.periodic_slots) if i not in used), None)
            if slot is None:
                raise RuntimeError(f"通道 {frame.channel} 的硬件定时任务已满。")
            if current:
                self._stop_periodic_tasks(((frame.channel, name),))
            worker = None
        self._set_hardware_periodic(frame, slot, period_ms, enabled=True)
        if worker is None:
            worker = HardwarePeriodicSend(frame, slot, period_ms)
        else:
            worker.frame = frame
            worker.period_ms = period_ms
        self.thread_messages[(frame.channel, name)] = {"data": signals, "func": worker, "wake": wake}

    def _set_hardware_periodic(self, frame, slot, period_ms, *, enabled):
        channel = frame.channel
        if channel in self._periodic_faults:
            raise RuntimeError(f"通道 {channel} 的定时状态未知，请先关闭通道或设备。")
        try:
            with self._io_lock:
                self._backend.set_periodic(frame, slot, period_ms, enabled=enabled)
        except (OSError, RuntimeError) as exc:
            # A missing reply does not prove the card rejected the change.
            # Stop the affected channel before reusing a possibly live slot.
            try:
                self.reset(channel)
            except (OSError, RuntimeError) as stop_error:
                self._periodic_faults.add(channel)
                error = f"通道 {channel} 无法确认停发：{stop_error}。请关闭设备。"
                for task in self.thread_messages.values():
                    worker = task["func"]
                    if worker.channel == channel:
                        worker.submitted = False
                        worker.error = error
                raise RuntimeError(f"硬件定时配置失败：{exc}。{error}") from exc
            raise RuntimeError(f"硬件定时配置失败，已停止通道 {channel}：{exc}") from exc

    def _default_period(self, message, task):
        if self.hardware_periodic:
            if task:
                return task["func"].period_ms
            return message.cycle_time or 100
        return 100

    def start_wake(self, channel, profile, *, background=False):
        with self._state_lock:
            if channel not in self.channels:
                raise RuntimeError(f"通道 {channel} 未打开。")
            if not profile:
                raise ValueError("请先保存唤醒配置。")
            prepared = []
            for name, selected in profile.items():
                message = self.db.get_message_by_name(name)
                task = self.thread_messages.get((channel, name))
                if task and isinstance(task["func"], PulseSend) and task["func"].is_alive():
                    raise RuntimeError(f"报文 {name} 正在脉冲发送，请等待完成或先取消。")
                previous = task["data"] if task and task["func"].is_alive() else None
                values, payload = encode_wake_message(message, selected, previous)
                frame = self._message(channel, message, payload, message.is_fd, False)
                prepared.append((name, values, frame, self._default_period(message, task)))
            try:
                for name, values, frame, period_ms in prepared:
                    self._schedule(name, values, frame, period_ms * 1_000_000, wake=True,
                                   background=background)
            except (RuntimeError, OSError):
                self.stop_wake()
                raise

    def stop_wake(self):
        with self._state_lock:
            self._stop_periodic_tasks(name for name, task in self.thread_messages.items()
                                      if task.get("wake"))

    def _stop_periodic_tasks(self, names):
        workers = [(name, self.thread_messages[name]["func"]) for name in names]
        software = [(name, worker) for name, worker in workers if isinstance(worker, PeriodicSend)]
        for _, worker in software:
            worker.cancel()
        for name, worker in software:
            worker.join()
            del self.thread_messages[name]
        for name, worker in workers:
            if isinstance(worker, HardwarePeriodicSend):
                self._set_hardware_periodic(worker.frame, worker.slot, worker.period_ms, enabled=False)
                worker.active = False
                del self.thread_messages[name]

    def _forget_hardware_tasks(self, channel=None):
        for name, task in tuple(self.thread_messages.items()):
            worker = task["func"]
            if isinstance(worker, HardwarePeriodicSend) and (channel is None or worker.channel == channel):
                worker.active = False
                del self.thread_messages[name]

    def periodic_payload(self, name: str, channel=0) -> bytes | None:
        """Snapshot the encoded frame used by the current periodic task."""
        with self._state_lock:
            task = self.thread_messages.get((channel, name))
            if task is None or not task["func"].is_alive() or not task["func"].submitted:
                return None
            return task["func"].payload

    @property
    def wake_active(self):
        with self._state_lock:
            return any(task.get("wake") and task["func"].is_alive()
                       for task in self.thread_messages.values())

    def _encode_signal_frame(self, channel, name, signals, can_fd, fd_brs):
        try:
            message, payload = self.db.get_single_array(name, signals)
        except cantools.database.EncodeError as exc:
            raise ValueError(f"报文 {name} 编码失败：{exc}") from exc
        return message, self._message(channel, message, payload, bool(can_fd), bool(fd_brs))

    def _require_send_channel(self, channel):
        if channel not in self.channels:
            raise RuntimeError(f"通道 {channel} 未打开。")
        if channel in self._periodic_faults:
            raise RuntimeError(f"通道 {channel} 的定时状态未知，请先关闭通道或设备。")

    def single_send(self, chn_index, name, single_data=None, state=True,
                    can_fd=True, fd_brs=False, cycle_time=100, *, background=False, send_count=-1):
        with self._state_lock:
            task = self.thread_messages.get((chn_index, name))
            if not state:
                if task:
                    self._stop_periodic_tasks(((chn_index, name),))
                return
            self._require_send_channel(chn_index)
            if cycle_time is not None and (type(cycle_time) is not int or cycle_time < 1):
                raise ValueError("发送间隔必须为正整数毫秒。")
            if send_count is not None and (type(send_count) is not int or send_count < -1 or send_count == 0):
                raise ValueError("发送次数必须为正整数，或使用 -1 持续发送。")
            signals = dict(task["data"]) if task else {}
            signals.update(single_data or {})
            message, frame = self._encode_signal_frame(chn_index, name, signals, can_fd, fd_brs)
            if cycle_time is None:
                cycle_time = self._default_period(message, task)
            self._schedule(name, signals, frame, cycle_time * 1_000_000, background=background, send_count=send_count)

    def send_timed_trigger(self, channel, name, signals, *, can_fd, fd_brs, cycle_time, stop_after):
        """Run a timed trigger and release using the current device send mode."""
        if type(cycle_time) is not int or cycle_time < 1:
            raise ValueError("发送间隔必须为正整数毫秒。")
        period_ns = cycle_time * 1_000_000
        period_s = cycle_time / 1000
        phase_count = -1 if self.hardware_periodic else 1
        with self._state_lock:
            self._require_send_channel(channel)
            current = self.thread_messages.get((channel, name))
            active = {**(current["data"] if current else {}), **signals}
            released = {**active, **dict.fromkeys(signals, 0)}
            message, active_frame = self._encode_signal_frame(channel, name, active, can_fd, fd_brs)
            for signal in signals:
                message.get_signal_by_name(signal)
            _, release_frame = self._encode_signal_frame(channel, name, released, can_fd, fd_brs)
            self._schedule(name, active, active_frame, period_ns, send_count=phase_count)
            task = self.thread_messages[(channel, name)]

        completed = False
        try:
            time.sleep(period_s)
            with self._state_lock:
                # An update or stop from another caller supersedes this sequence.
                if self.thread_messages.get((channel, name)) is not task:
                    return False
                self._schedule(name, released, release_frame, period_ns,
                               send_count=phase_count if stop_after else -1)
                task = self.thread_messages[(channel, name)]
            if stop_after and self.hardware_periodic:
                time.sleep(period_s)
            completed = True
        finally:
            with self._state_lock:
                owns_task = self.thread_messages.get((channel, name)) is task
                if owns_task and (stop_after or not completed) and channel not in self._periodic_faults:
                    self._stop_periodic_tasks(((channel, name),))
        return owns_task

    def start_pulse(self, channel, name, signals, count, *, can_fd):
        if type(count) is not int or count < 1:
            raise ValueError("脉冲发送次数必须为正整数。")
        if not signals:
            raise ValueError("请先在信号列表的「设置值」列填写要脉冲发送的信号。")
        with self._state_lock:
            task = self.thread_messages.get((channel, name))
            current = task["func"] if task else None
            sending = current is not None and current.is_alive()
            if sending and isinstance(current, PulseSend):
                raise RuntimeError(f"报文 {name} 正在脉冲发送。")
            if sending:
                channel = current.channel
                can_fd = current.frame.is_fd
            if channel not in self.channels or channel in self._periodic_faults:
                raise RuntimeError(f"通道 {channel} 不可用，请检查通道状态。")
            message = self.db.get_message_by_name(name)
            previous = task["data"] if task else None
            if current is not None and current.submitted:
                previous = message.decode(current.payload, decode_choices=False)
            _, trigger_payload = encode_wake_message(message, signals, previous)
            reset_signals = {}
            for signal_name, value in signals.items():
                definition = message.get_signal_by_name(signal_name)
                reset_signals[signal_name] = {"raw": 0} if isinstance(value, dict) and 0 in definition.choices else 0
            reset_values, reset_payload = encode_wake_message(message, reset_signals, previous)
            if trigger_payload == reset_payload:
                raise ValueError("触发值和复位值必须编码为不同的值。")
            brs = sending and current.frame.bitrate_switch
            trigger_frame = self._message(channel, message, trigger_payload, can_fd, brs)
            reset_frame = self._message(channel, message, reset_payload, can_fd, brs)
            period = current.period_ms if sending else message.cycle_time or 100
            period_ns = period * 1_000_000
            wake = bool(sending and task.get("wake"))
            continuous = sending and getattr(current, "remaining", None) is None
            if task:
                self._stop_periodic_tasks(((channel, name),))
            resume = None
            if continuous:
                def resume(worker):
                    self._resume_after_pulse(channel, name, worker, reset_values, reset_frame, period_ns, wake)
            worker = PulseSend(trigger_frame, reset_frame, period_ns, self._send_periodic, self.__log,
                               send_count=count, resume=resume)
            self.thread_messages[(channel, name)] = {"data": reset_values, "func": worker, "wake": wake}
            worker.start()

    def _resume_after_pulse(self, channel, name, worker, values, frame, period_ns, wake):
        # Stop/reset hold the state lock while joining. Cancellation must be
        # able to interrupt a pulse that is waiting to resume its old task.
        while not worker.cancelled:
            if self._state_lock.acquire(timeout=0.02):
                break
        else:
            return
        try:
            task = self.thread_messages.get((channel, name))
            if worker.cancelled or task is None or task["func"] is not worker:
                return
            del self.thread_messages[(channel, name)]
            try:
                self._schedule(name, values, frame, period_ns, wake=wake, background=True)
            except (RuntimeError, OSError, ValueError) as exc:
                worker.error = f"脉冲完成，恢复持续发送失败：{exc}"
                self.__log.error(worker.error)
                self.thread_messages[(channel, name)] = task
        finally:
            self._state_lock.release()

    @staticmethod
    def _frame_info(frame):
        fields = ("timestamp", "arbitration_id", "dlc", "is_rx", "is_fd", "channel",
                  "is_remote_frame", "is_extended_id", "is_error_frame", "bitrate_switch",
                  "error_state_indicator")
        return {**{field: getattr(frame, field) for field in fields}, "data": list(frame.data)}

    def _collect_frames(self):
        # Callers hold _rx_lock. DLL reads stay serialized with writes, while
        # decoding, queue management and BLF disk I/O do not hold _io_lock.
        with self._io_lock:
            batch = self._poll_raw(1024) if self._poll_raw is not None else self._backend.poll(1024)
        frames = self._backend.decode_poll(batch) if self._poll_raw is not None else batch
        for frame in frames:
            if self._continuous_rx and frame.is_error_frame:
                now = time.monotonic()
                if now - self._last_bus_error_log >= 1:
                    self.__log.warning(f"CAN 通道 {frame.channel} 收到总线错误帧。")
                    self._last_bus_error_log = now
            if self.custom_log is not None:
                self.custom_log.on_message_received(frame)
            self._pending.append(frame)
            if frame.is_rx and frame.arbitration_id == 0x7b3 and frame.channel in self._diagnostic_frames:
                self._diagnostic_frames[frame.channel].append(list(frame.data))
        return len(frames)

    def get_receive_data(self, chn_index=None):
        if chn_index is not None:
            validate_channel(chn_index)
        with self._rx_lock:
            self._collect_frames()
            frames = []
            remaining = deque(maxlen=self._pending.maxlen)
            for frame in self._pending:
                if chn_index is None or frame.channel == chn_index:
                    frames.append(self._frame_info(frame))
                else:
                    remaining.append(frame)
            self._pending = remaining
        if self.__monitor_switch:
            for frame in frames:
                self.message_monitor_filter(frame)
        return frames

    def message_monitor_filter(self, messgae_info: dict) -> None:
        """
        过滤信号
        :param  messgae_info: 接收到的单条can log
        :return:
        """
        for signal, info in self.mir_msgs.items():
            # 监听到can log中有此报文ID，进入判断
            if info['R_MessageID'] == messgae_info['arbitration_id']:  
                # 获取can log 该报文的目前值 键值对
                signal_data = self.db.get_single_value({"message_id":messgae_info['arbitration_id'], 
                                                        'data': messgae_info['data']}, True)
                # 获取指定信号的信号值，如果满足进入处理程序。
                if signal_data[signal] == int(info['R_Coding'], 16):  
                    # 根据ID获取要返回报文的报文名
                    name = self.db.get_message_by_id(info['T_MessageID']).name 
                    # 获取要发送报文类型
                    can_fd = info['T_CanType'] == 'CANFD'
                    # 获取发送间隔
                    cycle_time = int(info['T_Period'][:-2])
                    # print(cycle_time)
                    self.single_send(messgae_info['channel'], name,
                                     {info['T_SignalName']: int(info['T_Coding'], 16)},
                                     can_fd=can_fd, cycle_time=cycle_time)
                    
                
    def blf_start(self, logfilename: Optional[str] = ""):
        with self._state_lock:
            with self._rx_lock:
                if self._closed or not self.channels:
                    raise RuntimeError("请先打开通道。")
                if self.custom_log is not None:
                    raise RuntimeError("CANlog 正在录制。")
                name = logfilename or time.strftime("%Y%m%d%H%M%S", time.localtime())
                path = data_path("can_log") / f"{name}.blf"
                for index in count():
                    candidate = path if index == 0 else path.with_stem(f"{path.stem}_{index:02d}")
                    try:
                        stream = candidate.open("xb")
                    except FileExistsError:
                        continue
                    try:
                        self.custom_log = BLFWriter(stream)
                    except BaseException:
                        stream.close()
                        raise
                    break
            self._sync_receiver()

    def _sync_receiver(self):
        # The caller holds the state lock, but not the I/O lock when joining.
        needs_receiver = self.custom_log is not None or (self._continuous_rx and self.channels)
        if needs_receiver:
            if self._receive_thread is None or not self._receive_thread.is_alive():
                self._receive_stop.clear()
                self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
                self._receive_thread.start()
        else:
            self._stop_receiver()

    def _stop_receiver(self):
        if self._receive_thread is not None:
            self._receive_stop.set()
            self._receive_thread.join()
            self._receive_thread = None

    def _receive_loop(self):
        idle_interval = getattr(self._backend, "receive_interval", 0.05)
        last_error = float("-inf")
        while not self._receive_stop.is_set():
            try:
                with self._rx_lock:
                    self._collect_frames()
            except (OSError, RuntimeError) as exc:
                self.receive_error = str(exc)
                now = time.monotonic()
                if now - last_error >= 1:
                    self.__log.error(f"接收队列读取失败：{exc}")
                    last_error = now
                if getattr(exc, "backend_error_code", None) == -4:
                    return
                self._receive_stop.wait(0.25)
            else:
                self._receive_stop.wait(idle_interval)

    def _drain_recording_frames(self):
        # The caller holds _rx_lock. Bound the drain while the bus is active.
        empty = False
        try:
            for _ in range(32):
                has_frames = bool(self._collect_frames())
                # A USB read can yield no frames when it contains a partial packet.
                if empty and not has_frames:
                    return
                empty = not has_frames
        except (OSError, RuntimeError) as exc:
            self.__log.error(f"CANlog 收尾时读取接收队列失败：{exc}")
        else:
            self.__log.warning("CANlog 收尾读取达到批次上限，接收队列可能仍有数据。")

    def blf_stop(self):
        with self._state_lock:
            if self.custom_log is None:
                return
            try:
                with self._rx_lock:
                    try:
                        self._drain_recording_frames()
                    finally:
                        writer, self.custom_log = self.custom_log, None
                        writer.stop()
            finally:
                self._sync_receiver()

    def message_monitor(self, mir_msgs: dict):
        self.__monitor_switch = not self.__monitor_switch
        self.mir_msgs = mir_msgs

    def _discard_channel_frames(self, channel):
        self._pending = deque((frame for frame in self._pending if frame.channel != channel),
                              maxlen=self._pending.maxlen)
        self._diagnostic_frames[channel].clear()

    def clear_can_log(self, chn_index):
        validate_channel(chn_index)
        with self._rx_lock:
            with self._io_lock:
                self._backend.clear(chn_index)
            self._discard_channel_frames(chn_index)

    def _require_diagnostics(self):
        if not self.supports_diagnostics:
            if backend_mode() == "simulation":
                raise RuntimeError("模拟模式不支持 ECU 诊断。")
            raise RuntimeError("当前 USB 后端尚未开放 ECU 诊断。")

    def diag_send(self, chn_index: int, data: list):
        self._require_diagnostics()
        if len(data) != 8:
            raise ValueError("诊断请求必须为 8 字节。")
        # The existing Windows diagnostic wiring uses CAN0.
        def send(payload):
            self._send(can_Message(
                channel=0, arbitration_id=0x773, is_extended_id=False,
                data=payload, check=True,
            ))
        send(data)
        if data[1] == 0x22:
            time.sleep(0.01)
            send([0x30, 0, 0x14, 0, 0, 0, 0, 0])

    def diag_receive(self, chn_index: int) -> list:
        self._require_diagnostics()
        validate_channel(chn_index)
        with self._rx_lock:
            self._collect_frames()
            pending = self._diagnostic_frames[0]
            frames = list(pending)
            pending.clear()
            return frames

    def diag_config_analysis_list(self, chn_index: int)-> list:
        """
        解析成数字列表格式的配置字
        :param data: 诊断数据
        :return:  数字列表格式的配置字
        """

        config_data = self.diag_receive(chn_index)
        config_content = []
        for num, data in enumerate(config_data):
            if num == 0:
                config_content += [i for i in data['data'][3:]]
            elif num == len(config_data) - 1:
                config_content += [data['data'][1]]
            else:
                config_content += [i for i in data['data'][1:]]
        
        return config_content
    
    def diag_config_analysis_chars(self, chn_index: int)-> str:
        """
        解析成字符串格式的配置字
        :return:  字符串格式的配置字
        """
        data = self.diag_config_analysis_list(chn_index)
        # print(len(data))
        return " ".join(hex(i)[2:].upper() if len(hex(i)[2:]) > 1 else "0"+hex(i)[2:].upper() for i in data)

    def __diag_seed_analysis(self, chn_index: int) -> list:
        """
        该函数用于种子解析
        :return:  None
        """
        config_data = self.diag_receive(chn_index)

        config_content = config_data[0]['data'][3:-1]

        self.__log.debug(f"种子为{config_content}")
        return config_content

    
    def __seed_to_key(self, seed: int, mask: hex= 0x48205554) -> str:
        """
        该函数用于计算解密的key
        :param seed: 种子
        :param mask: 掩码
        :return:  解密的数据
        """
        key:str = ""
        # print(seed)
        if seed != 0:
            for i in range(35):
                if seed & 0x80000000:
                    seed = seed << 1
                    seed = seed ^ mask
                else:
                    seed = seed << 1
            key = hex(seed)[-8:]

        self.__log.debug(f"种子解析结果：{key}")
        return key

    
    def _diag_send_key_27_02(self, chn_index: int) -> None:
        """
        该函数用于发送解密诊断
        """
        seed_array = self.__diag_seed_analysis()
        seed = seed_array[0] << 24
        seed += seed_array[1] << 16
        seed += seed_array[2] << 8
        seed += seed_array[3]
        key = self.__seed_to_key(seed)
        key1 = int("0x" + key[0:2], 16)
        key2 = int("0x" + key[2:4], 16)
        key3 = int("0x" + key[4:6], 16)
        key4 = int("0x" + key[6:], 16)
        send_27_02 = [0x06, 0x27, 0x02, key1, key2, key3, key4, 0x00]
        self.diag_send(send_27_02)
        time.sleep(1)
        send_result = self.diag_receive(chn_index)
        self.__log.debug(f"发送27_02的结果是：{send_result}")

    
    def _diag_seng_config(self, chn_index: int, config_array: list) -> None:
        """
        该函数用于写入配置字
        :param config_array: 配置字列表
        :return:
        """
        if len(config_array) != 200:
            self.__log.error(f"配置字数量有误，请检查配置字")
            return
        config_array += [0,0,0,0,0,0,0]
        conf = lambda : config_array.pop(0)
        self.diag_send([0x10, 0xCB, 0x2E, 0xF1, 0xB1]+[conf() for i in range(3)])
        for i in range(15):
            self.diag_send([0x21+i] + [conf() for i in range(7)])
        for i in range(14):
            self.diag_send([0x20+i] + [conf() for i in range(7)])
        time.sleep(3)
        self.diag_receive(chn_index)


    
    def diag_seng_configs(self, chn_index: int, config_array: list) -> None:
        """
        该函数用于写如配置字
        :param config_array: 配置字列表
        :return:
        """
        self._require_diagnostics()
        self.diag_send([0x02, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
        time.sleep(1)
        self.diag_send([0x02, 0x10, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00])
        time.sleep(1)
        self.diag_receive(chn_index)
        self.diag_send([0x02, 0x27, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
        time.sleep(1)
        self._diag_send_key_27_02(chn_index)
        self._diag_seng_config(chn_index, config_array)
    

    def chn_close_setup(self, channel=None, *, software_only=False):
        with self._state_lock:
            self._stop_periodic_tasks(name for name, task in self.thread_messages.items()
                                      if (channel is None or task["func"].channel == channel)
                                      and (not software_only or isinstance(task["func"], PeriodicSend)))


if __name__ == '__main__':
    # import pprint
    # print = pprint.pprint
    # print(12344)
    dbc = DBC("./config/EC24EV-006-014DB01  CAN DataBase for SC-CANFD_V4.4.dbc")
    
    zlg = zlgCan(0, "./config/EC24EV-006-014DB01  CAN DataBase for SC-CANFD_V4.4.dbc")
    # zlg.blf_start("test_write3")
    zlg.chn_open(0)
    # zlg.clear_can_log(0)
    # print(dbc.get_single_array("ESP_FD2",{"VehSpd":100, "VehSpdVld": 1}))
    # zlg.single_send(0,"ESP_FD2",{"VehSpd":90, "VehSpdVld": 1})
    # time.sleep(10)
    # zlg.single_send(0,'PEPS2',{'SysPowerMod': 2, 'SysPowerModVld': 1})
    # zlg.single_send(0,name= 'GW_NM', single_data={}, can_fd=False)
    # def rec_func():
    #     while True:
    #         zlg.get_receive_data(0)
            
    # rec = threading.Thread(target=rec_func)
    # rec.setDaemon(True)
    # rec.start()
    # time.sleep(300)
    # zlg.close()
    # zlg.clear_can_log()
    # zlg.message_monitor({
    #     'SysPowerMod':{
    #         'R_MessageID': '0x295',
    #         'R_Coding': '0x02',
    #         'T_SignalName':'SysPowerModVld',
    #         'T_MessageID': '0x295',
    #         'T_Period': '50ms',
    #         'T_CanType': 'CANFD',
    #         'T_Coding': '0x00'
    #     }
    # })
    # time.sleep(10)
    # zlg.clear_can_log()
    # zlg.diag_send([0x03, 0x22, 0xf1, 0xb1, 0x00, 0x00, 0x00, 0x00])
    # zlg.diag_send([0x30, 0x00, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00])
    # time.sleep(1)
    # print(json.dumps(zlg.get_receive_data()), file = open('./can_log/abc.json', 'w'),)
    # print(zlg.diag_config_analysis_list())
    # zlg.diag_seng_configs([71, 105, 19, 7, 1, 4, 0, 5, 96, 81, 53, 97, 88, 17, 84, 64, 65, 97, 32, 68, 137, 160, 5, 5, 33, 33, 66, 13, 144, 0, 0, 59, 72, 81, 16, 0, 18, 52, 83, 10, 0, 5, 2, 21, 1, 41, 44, 20, 3, 3, 68, 84, 1, 64, 39, 35, 1, 0, 0, 0, 0, 16, 0, 33, 64, 196, 52, 9, 155, 5, 144, 40, 32, 0, 0, 10, 100, 80, 56, 82, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    # time.sleep(20)
    # print(zlg.diag_config_analysis_list())
    # pprint.pprint(zlg.get_receive_data())
    # uds = ZUDS()
    # uds_handle = uds.Init()
    # zuds_config = ZUDS_ISO15765_PARAM()
    # zuds_config.block_size = 0
    # zuds_config.fill_byte = 0
    # zuds_config.local_st_min = int('0x7F', 16)
    # zuds_config.is_modify_ecu_st_min = 0
    # zuds_config.remote_st_min = int('0x7F', 16)
    # zuds_config.frame_type = 1
    # zuds_config.version = 0
    # zuds_config.max_data_len = 8
    # uds.SetParam(uds_handle, PARAM_TYPE_ISO15765, zuds_config)
    # sparam = ZUDS_SESSION_PARAM()
    # sparam.enhanced_timeout = 5000
    # sparam.timeout = 2000
    # uds.SetParam(uds_handle, PARAM_TYPE_SESSION, sparam)

    # zlg.diag_send([0x03, 0x22, 0xf1, 0xb1, 0x00, 0x00, 0x00, 0x00])
    # zlg.diag_send([0x30, 0x00, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00])
    # time.sleep(1)
    # print(zlg.diag_config_analysis_list())
    # zuds_frame =  ZUDS_FRAME()
    # zuds_frame.id = 0x773
    # zuds_frame.extend = 0
    # zuds_frame.remote = 0
    # zuds_frame.data_len = 8
    # zuds_frame.data[0] = 0x03
    # zuds_frame.data[1] = 0x22
    # zuds_frame.data[2] = 0xf1
    # zuds_frame.data[3] = 0xb1
    # zuds_frame.data[4] = 0x00
    # zuds_frame.data[5] = 0x00
    # zuds_frame.data[6] = 0x00
    # zuds_frame.data[7] = 0x00
    # uds.SetTransmitHandler(uds_handle, 0, zuds_frame, 8, zlg.diag_OnFunc)


    # req = ZUDS_REQUEST()
    # req.src_addr = 0x773
    # req.dst_addr = 0x7b3
    # req.suppress_response = 0
    # req.sid = 0x22 # 服务ID
    # req.param_len = 8
    # req.param[0] = 0x03
    # req.param[1] = 0x22
    # req.param[2] = 0xf1
    # req.param[3] = 0xb1
    # req.param[4] = 0x00
    # req.param[5] = 0x00
    # req.param[6] = 0x00
    # req.param[7] = 0x00
    # uds.Request(uds_handle, req)
    # print(uds_handle)

    zlg.diag_send(0, [0x02, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
    time.sleep(1)
    print(zlg.diag_receive(0))
