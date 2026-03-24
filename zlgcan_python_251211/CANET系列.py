'''
    支持的设备有 CANET-E-U，CANET-2/4/8E-U,CANDTU以太网系列(-E)，CANWIFI-200T
    所有配置 只能由上位机软件ZNETCOM进行配置
    ZNETCOM下载链接:    https://manual.zlg.cn/server/index.php?s=/api/attachment/visitFile/sign/1605987892478dfee87f21e616048fcc
'''

from zlgcan import *
import threading
import time

thread_flag = True
print_lock = threading.Lock()   # 线程锁，只是为了打印不冲突

# 目标端口/连接端口 PC作为TCP Server 时不需要关心这个参数
work_port = ["4001","4002"]
# 本地端口 PC作为TCP Client 时不需要关心此参数
local_port = ["8001","8002"]

# PC作为TCP服务器 CANET作为TCP客户端 --PC作为多个服务器     can_number传入连接该服务器的，客户端数量
def start_server(can_number=1):

    device_handles = []
    chn_handles = []

    for i in range(can_number):
        # 打开设备 并不会实际建立连接
        device_handle = zcanlib.OpenDevice(ZCAN_CANETTCP,i,0)
        if device_handle == INVALID_DEVICE_HANDLE:
            print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
            exit(0)
        print(f"设备 {i} 句柄: {device_handle}.")

        ret = zcanlib.ZCAN_SetValue(device_handle,"0/work_mode", "1".encode("utf-8"))   #0-客户端 1-服务器
        if ret == ZCAN_STATUS_ERR:
            print("设置工作模式失败！")

        ret = zcanlib.ZCAN_SetValue(device_handle, "0/local_port", local_port[i].encode("utf-8"))
        if ret == ZCAN_STATUS_ERR:
            print("设置本地端口失败！")

        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

        chn_handle = zcanlib.InitCAN(device_handle, i, chn_init_cfg)
        if chn_handle is None:
            print("启动通道%d失败！" % i)
            exit(0)
        print("通道句柄: %d." % chn_handle)

        ret = zcanlib.StartCAN(chn_handle)
        if ret == ZCAN_STATUS_ERR:
            print(f"StartCAN{i} fail ,连接设备失败，请确保目标IP和目标端口是否填写正确，并确保PC与以太网CAN卡处于同一网段")
        chn_handles.append(chn_handle)  # 将通道句柄添加到列表中
        device_handles.append(device_handle)

    # 等待客户端连接
    time.sleep(3)

    return device_handles, chn_handles

# PC作为TCP客户端 CANET作为TCP服务器 --PC作为多个客户端     can_number传入连接该服务器的绑定的通道数
def start_client(can_number=1):

    device_handles = []
    chn_handles = []

    for i in range(can_number):
        # 打开设备 并不会实际建立连接
        device_handle = zcanlib.OpenDevice(ZCAN_CANETTCP,i,0)
        if device_handle == INVALID_DEVICE_HANDLE:
            print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
            exit(0)
        print(f"设备{i}句柄: {device_handle}.")

        # 没写则不需要设置
        ret = zcanlib.ZCAN_SetValue(device_handle,"0/work_mode", "0".encode("utf-8"))   #0-客户端 1-服务器
        if ret == ZCAN_STATUS_ERR:
            print("设置工作模式失败！")

        ret = zcanlib.ZCAN_SetValue(device_handle, "0/work_port", work_port[i].encode("utf-8"))  # 目标端口
        if ret == ZCAN_STATUS_ERR:
            print("设置目标端口失败！")

        # 配置目标IP，需要与电脑网口保持同一网段
        ret = zcanlib.ZCAN_SetValue(device_handle, "0/ip", "192.168.0.178".encode("utf-8"))
        if ret == ZCAN_STATUS_ERR:
            print("设置目标IP失败！")

        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

        chn_handle = zcanlib.InitCAN(device_handle, i, chn_init_cfg)
        if chn_handle is None:
            print("启动通道%d失败！" % i)
            exit(0)
        print("通道句柄: %d." % chn_handle)

        ret = zcanlib.StartCAN(chn_handle)
        if ret == ZCAN_STATUS_ERR:
            print(f"StartCAN{i} fail ,连接设备失败，请确保目标IP和目标端口是否填写正确，并确保PC与以太网CAN卡处于同一网段")
        chn_handles.append(chn_handle)  # 将通道句柄添加到列表中
        device_handles.append(device_handle)

    return device_handles, chn_handles

# 同理 UDP通讯 多通道
def start_UDP(can_number=1):

    device_handles = []
    chn_handles = []

    for i in range(can_number):
        # 打开设备 并不会实际建立连接
        device_handle = zcanlib.OpenDevice(ZCAN_CANETUDP,i,0)
        if device_handle == INVALID_DEVICE_HANDLE:
            print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
            exit(0)
        print(f"设备句柄: {device_handle}.")

        # 没写则不需要设置
        ret = zcanlib.ZCAN_SetValue(device_handle,"0/local_port", local_port[i].encode("utf-8"))   # 设置本地端口
        # if ret == ZCAN_STATUS_ERR:
        #     print("设置本地端口失败！")

        ret = zcanlib.ZCAN_SetValue(device_handle, "0/work_port", work_port[i].encode("utf-8"))  # 设置目标端口
        if ret == ZCAN_STATUS_ERR:
            print("设置目标端口失败！")

        # 配置目标IP，需要与电脑网口保持同一网段
        ret = zcanlib.ZCAN_SetValue(device_handle, "0/ip", "192.168.0.178".encode("utf-8"))
        if ret == ZCAN_STATUS_ERR:
            print("设置目标IP失败！")

        # 初始化结构体参数无效，根据配置库决定
        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

        chn_handle = zcanlib.InitCAN(device_handle, i, chn_init_cfg)
        if chn_handle is None:
            print("启动通道%d失败！" % i)
            exit(0)
        print("通道句柄: %d." % chn_handle)

        ret = zcanlib.StartCAN(chn_handle)
        if ret == ZCAN_STATUS_ERR:
            print(f"StartCAN{i} fail ,连接设备失败，请确保目标IP和目标端口是否填写正确，并确保PC与以太网CAN卡处于同一网段")
        chn_handles.append(chn_handle)  # 将通道句柄添加到列表中
        device_handles.append(device_handle)

    return device_handles, chn_handles

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
        # msgs[i].frame.can_id |= 1 << 30
        msgs[i].frame.can_dlc = 8       # 数据长度
        for j in range(msgs[i].frame.can_dlc):
            msgs[i].frame.data[j] = j
    ret = zcanlib.Transmit(chn_handle, msgs, transmit_num)
    with print_lock: print(f"通道 {chn_handle & 0xFF} 成功发送 {ret} 条CAN报文")

if __name__ == "__main__":
    zcanlib = ZCAN()

    threads = []

    # PC启动多个服务器
    # handles,chn_handles = start_server(2)

    # PC作为多个客户端，去连接CANET服务器
    # handles, chn_handles = start_client(2)

    # UDP通讯，多连接
    handles, chn_handles = start_UDP(2)

    # 所有通道的数据需要各自开一个线程接收
    for i in range(len(chn_handles)):
        thread = threading.Thread(target=receive_thread, args=(handles[i], chn_handles[i]))  # 开启独立接收线程
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
    for i in range(len(handles)):
        ret = zcanlib.CloseDevice(handles[i])
        if ret == 1:
            print(f"关闭设备{i}成功")