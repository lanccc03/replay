'''
    支持的设备有 PCI-5010-U、PCI-5020-U、USBCAN-E-U、USBCAN-2E-U、USBCAN-4E-U、USBCAN-8E-U。
    注意在opendevice处选择对应型号的设备
'''

from zlgcan import *
import threading
import time

thread_flag = True
print_lock = threading.Lock()   # 线程锁，只是为了打印不冲突
is_auto_send = False        # 定时发送标识

#读取设备信息
def Read_Device_Info(device_handle):

    # 获取设备信息
    info = zcanlib.GetDeviceInf(device_handle)
    if info == None:
        print("获取设备信息失败！")
        exit(0)
    print("设备信息: \n%s" % info)

    can_number = info.can_num
    return can_number

# 接收线程
def receive_thread(device_handle,chn_handle):

    # 方便打印对齐 --无实际作用
    CANType_width = len("CANFD加速    ")
    id_width = len(hex(0x1FFFFFFF))

    while thread_flag:
        time.sleep(0.005)
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)  # CAN
        if rcv_num:
            if rcv_num > 100 :
                rcv_msg, rcv_num = zcanlib.Receive(chn_handle, 100,100)
            else :
                rcv_msg, rcv_num = zcanlib.Receive(chn_handle, rcv_num, 100)
            with print_lock:
                for msg in rcv_msg[:rcv_num]:
                    can_type = "CAN   "
                    frame = msg.frame
                    direction = "TX" if frame._pad & 0x20 else "RX"
                    frame_type = "扩展帧" if frame.can_id & (1 << 31) else "标准帧"
                    frame_format = "远程帧" if frame.can_id & (1 << 30) else "数据帧"
                    can_id = hex(frame.can_id & 0x1FFFFFFF)

                    if frame.can_id & (1 << 30):
                        data = ""
                        dlc = 0
                    else:
                        dlc = frame.can_dlc
                        data = " ".join([f"{num:02X}" for num in frame.data[:dlc]])

                    print(f"[{msg.timestamp}] CAN{chn_handle & 0xFF} {can_type:<{CANType_width}}\t{direction} ID: {can_id:<{id_width}}\t{frame_type} {frame_format}"
                          f" DLC: {dlc}\tDATA(hex): {data}")
    print("=====")

# 启动通道
def USBCANEU_Start(zcanlib, device_handle, chn):

    # 波特率
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/baud_rate", "500000".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d baud failed!" % chn)
        return None

    # 自定义波特率    当产品波特率对采样点有要求，或者需要设置非常规波特率时使用   ---默认不管
    # ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "//baud_rate_custom", "500Kbps(80%),2.0Mbps(80%),(80,07C00002,01C00002)".encode("utf-8"))
    # if ret != ZCAN_STATUS_OK:
    #     print("Set CH%d baud failed!" % chn)
    #     return None

    # 初始化通道
    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    chn_init_cfg.can_type = ZCAN_TYPE_CAN  # USBCAN 必须选择CAN
    chn_init_cfg.config.canfd.mode = 0  # 0-正常模式 1-只听模式
    chn_handle = zcanlib.InitCAN(device_handle, chn, chn_init_cfg)
    if chn_handle is None:
        print("initCAN failed!" % chn)
        return None

    # 启动通道
    ret = zcanlib.StartCAN(chn_handle)
    if ret != ZCAN_STATUS_OK:
        print("startCAN failed!" % chn)
        return None

    return chn_handle

# 设置滤波  白名单过滤(只接收范围内的数据)    USBCAN-E设备支持设置最多32组滤波   8E-U不支持此配置
def Set_Filter(device_handle,chn):

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_clear", "0".encode("utf-8")) # 清除滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_clear failed!" % chn)
        return None

    # 流程为：for【set_mode(与前一组滤波同类型可以省略) + set_start + set_end】+ ack
    # 这里设置第一道滤波：标准帧0~0x7F，第二道滤波：标准帧0xFF~0x1FF，第三道滤波：扩展帧0xFF~0x2FF
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_mode", "0".encode("utf-8"))  # 设置滤波模式 标准帧滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_mode failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_start", "0".encode("utf-8")) # 设置白名单范围 起始ID
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_start failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_end", "0x7F".encode("utf-8"))   # 设置白名单范围 结束ID
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_end failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_start", "0xFF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_start failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_end", "0x1FF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_end failed!" % chn)
        return None

    # 扩展帧滤波
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_mode", "1".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_mode failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_start", "0xFF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_start failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_end", "0x2FF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_end failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_ack", "0".encode("utf-8"))  # 使能滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_ack failed!" % chn)
        return None

# 关闭定时发送
def Clear_Auto_Send(device_handle,chn):

    if is_auto_send:
        ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/clear_auto_send", "0".encode("utf-8"))
        if ret != ZCAN_STATUS_OK:
            print("Clear CH%d USBCAN-xE-U AutoSend failed!" % (chn))
            exit(0)

# 定时发送 设置   每通道最多32条    设置后立即生效，不需要apply使能  定时发送开启过程中调用Tramit发送函数无效
def Auto_Send_test(device_handle,chn):

    Clear_Auto_Send(device_handle,chn)  # 先清空再设置
    global is_auto_send    # 引用全局变量
    is_auto_send = True    # 标识开启定时发送

    auto_can = ZCAN_AUTO_TRANSMIT_OBJ()
    memset(addressof(auto_can), 0, sizeof(auto_can))
    auto_can.index = 0      # 定时发送序列号 用于标记这条报文
    auto_can.enable = 1     # 使能该条定时发送 0-关闭 1-启用
    auto_can.interval = 100 # 定时周期，单位ms

    # auto_can.obj 同 ZCAN_Transmit_Data结构体
    auto_can.obj.transmit_type = 0
    auto_can.obj.frame.can_id = 17  # id
    # auto_can.obj.frame.can_id |= 1 << 31    # 扩展帧
    auto_can.obj.frame.can_dlc = 8  # 数据长度
    auto_can.obj.frame._pad |= 0x20  # 发送回显
    for j in range(auto_can.obj.frame.can_dlc):
        auto_can.obj.frame.data[j] = j

    # 设置后立即生效
    ret = zcanlib.ZCAN_SetValue(device_handle,str(chn)+"/auto_send",byref(auto_can))
    if ret != ZCAN_STATUS_OK:
        print("设置定时发送 CAN%d 失败!" % chn)
        return None

# 发送示例
def Transmit_Test(chn_handle):
    # 发送 CAN 报文
    transmit_num = 10
    msgs = (ZCAN_Transmit_Data * transmit_num)()
    memset(addressof(msgs),0,sizeof(msgs))
    for i in range(transmit_num):
        msgs[i].transmit_type = 0       # 0-正常发送，2-自发自收
        msgs[i].frame.can_id = 10       # 发送id
        msgs[i].frame.can_id |= 1<<31   # 最高位(bit31)为 扩展帧/标准帧 标识位 同理 bit30为 数据帧/远程帧
        msgs[i].frame.can_dlc = 8       # 数据长度
        for j in range(msgs[i].frame.can_dlc):
            msgs[i].frame.data[j] = j
    ret = zcanlib.Transmit(chn_handle, msgs, transmit_num)
    with print_lock: print(f"通道 {chn_handle & 0xFF} 成功发送 {ret} 条CAN报文")

# 通道转发示例    ---可调用多次 (如2收 3,4转发)
def Redirect_Test(device_handle,input_chn,out_chn):
    # 将通道2收到的数据从3通道中发出来     ---源:"2/redirect"   目标:"3 1"    “3 1”-3通道转发 “3 0”-3通道不转发
    ret = zcanlib.ZCAN_SetValue(device_handle, str(input_chn) + "/redirect", (str(out_chn) + " 1").encode('utf-8'))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d Redirect failed!" % (input_chn))
        return None

if __name__ == "__main__":
    zcanlib = ZCAN()

    # 打开设备
    handle = zcanlib.OpenDevice(ZCAN_USBCAN_2E_U, 0, 0)
    if handle == INVALID_DEVICE_HANDLE:
        print("打开设备失败！")
        exit(0)
    print("设备句柄: %d." % handle)

    # 获取设备信息
    can_number = Read_Device_Info(handle)

    # 启动通道
    chn_handles = []
    threads = []
    for i in range(can_number):
        chn_handle = USBCANEU_Start(zcanlib, handle, i)
        if chn_handle is None:
            print("启动通道%d失败！" % i)
            exit(0)
        chn_handles.append(chn_handle)  # 将通道句柄添加到列表中
        print("通道句柄: %d." % chn_handle)

    # 所有通道的数据需要各自开一个线程接收
    for i in range(len(chn_handles)):
        thread = threading.Thread(target=receive_thread, args=(handle, chn_handles[i]))  # 开启独立接收线程
        threads.append(thread)
        thread.start()

    # 通道转发
    # Redirect_Test(handle, 0, 3)
    # Redirect_Test(handle, 1, 2)

    # 设置滤波  USBCAN-8E-U无法通过此函数设置滤波
    # Set_Filter(handle, 0)

    # 设置定时发送    (USBCAN-2E-U)定时发送使能之后，不能调用发送函数(这样会导致程序异常)。
    Auto_Send_test(handle,0)
    time.sleep(1)
    Clear_Auto_Send(handle, 0)  # 需要关闭定时发送后，才能调用普通函数

    # 发送报文示例
    Transmit_Test(chn_handles[1])
    Transmit_Test(chn_handles[0])

    # 回车退出
    input()
    thread_flag = False
    Clear_Auto_Send(handle,0)
    Transmit_Test(chn_handles[0])

    # 关闭接收线程
    for i in range(len(chn_handles)):
        threads[i].join()

    # 关闭通道
    for i in range(len(chn_handles)):
        ret = zcanlib.ResetCAN(chn_handles[i])
        if ret == 1:
            print(f"关闭通道{i}成功")

    # 关闭设备
    ret = zcanlib.CloseDevice(handle)
    if ret == 1:
        print("关闭设备成功")