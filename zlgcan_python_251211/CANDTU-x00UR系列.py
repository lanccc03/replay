'''
    支持的设备有 CANDTU-100UR，CANDTU-200UR， CANDTU-200UWGR
    start_CAN之后必要的进行延迟，以保证后续能正常控制通道句柄
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
def CANDTU_Start(zcanlib, device_handle, chn):

    # 波特率
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/baud_rate", "500000".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d baud failed!" % chn)
        return None

    # 终端电阻
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/initenal_resistance", "1".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Open CH%d resistance failed!" % chn)
        return None

    # 工作模式
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/work_mode", "1".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Open CH%d resistance failed!" % chn)
        return None

    # 滤波    --- 有需要再设置，全部接收则按以下填
    # 验收码
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/acc_code", "0x00000000".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Open CH%d resistance failed!" % chn)
        return None

    # 屏蔽码
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/acc_mask", "0xFFFFFFFF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Open CH%d resistance failed!" % chn)
        return None

    # 初始化通道
    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    # chn_init_cfg.can_type = ZCAN_TYPE_CAN           # USBCAN 必须选择CAN
    # chn_init_cfg.config.can.mode = 0                # 0-正常模式 1-只听模式
    # chn_init_cfg.config.can.acc_code = 0            # 默认参数
    # chn_init_cfg.config.can.acc_mask = 0xFFFFFFFF   # 默认参数
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

if __name__ == "__main__":
    zcanlib = ZCAN()

    # 打开设备
    handle = zcanlib.OpenDevice(ZCAN_CANDTU_200UR, 0, 0)
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
        chn_handle = CANDTU_Start(zcanlib, handle, i)
        time.sleep(1)   # 必要的延时 让后续能正常操作设备句柄
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

    # 发送报文示例
    Transmit_Test(chn_handles[1])
    Transmit_Test(chn_handles[0])

    # 回车退出
    input()
    thread_flag = False

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