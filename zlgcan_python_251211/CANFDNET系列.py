'''
    支持的设备有 CANFDNET-100 MINI，CANFDNET-200U/400U/800U    CANFDNET-200H,400H  CANFDDTU-400系列及以上
    启动设备的连接本质，其实是建立网络连接，所有请在ZCANPRO/ZXDOC 我们官方上位机软件上跑通再进行例程测试
    Line 17 and 19 对应目标端口与本地端口，这里填写很重要，需要根据网络配置工具的设置进行配置

    软件下载链接：https://manual.zlg.cn/web/#/314/12431
    CANFDNET系列使用方法：https://manual.zlg.cn/web/#/151/5352
'''

from zlgcan import *
import threading
import time

thread_flag = True
print_lock = threading.Lock()   # 线程锁，只是为了打印不冲突
enable_merge_receive = 0        # 合并接收标识
enable_bus_usage = 0            # 总线利用率标识

# 目标端口/连接端口 PC作为TCP Server 时不需要关心这个参数
work_port = ["8000","8001"]
# 本地端口 PC作为TCP Client 时不需要关心此参数
local_port = ["4001","4002"]

# 读取设备信息
def Read_Device_Info(device_handle):

    # 获取设备信息
    info = zcanlib.GetDeviceInf(device_handle)
    print("设备信息: \n%s" % info)

    can_number = info.can_num
    return can_number

# PC作为TCP服务器 CANFDNET作为TCP客户端 --PC作为1个服务器     can_number传入连接该服务器的，客户端数量
def start_server_only(can_number=1):

    device_handles = []
    chn_handles = []

    # 打开设备 并不会实际建立连接
    device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_200U_TCP,0,0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
        exit(0)
    print("设备句柄: %d." % device_handle)

    ret = zcanlib.ZCAN_SetValue(device_handle,"0/work_mode", "1".encode("utf-8"))   #0-客户端 1-服务器
    if ret == ZCAN_STATUS_ERR:
        print("设置工作模式失败！")

    ret = zcanlib.ZCAN_SetValue(device_handle, "0/local_port", local_port[0].encode("utf-8"))
    if ret == ZCAN_STATUS_ERR:
        print("设置本地端口失败！")

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

    for i in range(can_number):
        ret = zcanlib.ZCAN_SetValue(device_handle, str(i) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set CH%d  set_device_tx_echo failed!" % (i))
            return None
        chn_handle = zcanlib.InitCAN(device_handle, i, chn_init_cfg)
        if chn_handle is None:
            print("启动通道%d失败！" % i)
            exit(0)
        chn_handles.append(chn_handle)  # 将通道句柄添加到列表中
        print("通道句柄: %d." % chn_handle)

        ret = zcanlib.StartCAN(chn_handle)
        if ret == ZCAN_STATUS_ERR:
            print(f"StartCAN{i} fail ,连接设备失败，请确保目标IP和目标端口是否填写正确，并确保PC与以太网CAN卡处于同一网段")

    # 等待客户端连接
    # 方法一:
    # time.sleep(3)

    # 方法二:  通过一直判断设备硬件版本号 来确定CANFDNET是否连上
    hw_version = zcanlib.GetDeviceInf(device_handle).hw_version
    info = ZCAN_DEVICE_INFO()
    for i in range(3000):
        info = zcanlib.GetDeviceInf(device_handle)
        if info.hw_version != hw_version:
            print(f"连接耗时{i}ms")
            break
        time.sleep(0.001)
    print("设备信息: \n%s" % info)
    if info.hw_version == hw_version:
        print("连接超时:请确保本地端口是否填写正确，并确保PC与以太网CAN卡处于同一网段")
        exit(0)

    device_handles.append(device_handle)

    return device_handles, chn_handles

# PC作为TCP服务器 CANFDNET作为TCP客户端 --PC作为多个服务器     can_number传入连接该服务器的，客户端数量
def start_server_all(can_number=1):

    device_handles = []
    chn_handles = []

    for i in range(can_number):
        # 打开设备 并不会实际建立连接
        device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_200U_TCP,i,0)
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

        ret = zcanlib.ZCAN_SetValue(device_handle, str(0) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set DH%d  set_device_tx_echo failed!" % (i))
            return None
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
    # 方法一:
    # time.sleep(3)

    # 方法二:  通过一直判断设备硬件版本号 来确定CANFDNET是否连上
    hw_version = zcanlib.GetDeviceInf(device_handles[can_number-1]).hw_version
    info = ZCAN_DEVICE_INFO()
    for i in range(3000):
        info = zcanlib.GetDeviceInf(device_handles[can_number-1])
        if info.hw_version != hw_version:
            print(f"连接耗时{i}ms")
            break
        time.sleep(0.001)
    print("设备信息: \n%s" % info)
    if info.hw_version == hw_version:
        print("连接超时:请确保本地端口是否填写正确，并确保PC与以太网CAN卡处于同一网段")
        exit(0)

    return device_handles, chn_handles

# PC作为TCP客户端 CANFDNET作为TCP服务器 --PC作为一个客户端     can_number传入连接该服务器的绑定的通道数
def start_client_only(can_number=1):

    device_handles = []
    chn_handles = []

    # 打开设备 并不会实际建立连接
    device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_200U_TCP,0,0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
        exit(0)
    print(f"设备句柄: {device_handle}.")

    # 没写则不需要设置
    ret = zcanlib.ZCAN_SetValue(device_handle,"0/work_mode", "0".encode("utf-8"))   #0-客户端 1-服务器
    if ret == ZCAN_STATUS_ERR:
        print("设置工作模式失败！")

    ret = zcanlib.ZCAN_SetValue(device_handle, "0/work_port", work_port[0].encode("utf-8"))  # 设置目标端口
    if ret == ZCAN_STATUS_ERR:
        print("设置目标端口失败！")

    # 配置目标IP，需要与电脑网口保持同一网段
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/ip", "192.168.0.177".encode("utf-8"))
    if ret == ZCAN_STATUS_ERR:
        print("设置目标IP失败！")

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

    for i in range(can_number):
        ret = zcanlib.ZCAN_SetValue(device_handle, str(i) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set CH%d  set_device_tx_echo failed!" % (i))
            return None

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

    Read_Device_Info(device_handle)

    return device_handles, chn_handles

# PC作为TCP客户端 CANFDNET作为TCP服务器 --PC作为多个客户端     can_number传入连接该服务器的绑定的通道数
def start_client_all(can_number=1):

    device_handles = []
    chn_handles = []

    for i in range(can_number):
        # 打开设备 并不会实际建立连接
        device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_200U_TCP,i,0)
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

        ret = zcanlib.ZCAN_SetValue(device_handle, str(0) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set CH%d  set_device_tx_echo failed!" % (i))
            return None

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

    # 读取设备信息
    Read_Device_Info(device_handles[can_number-1])

    return device_handles, chn_handles

# 同理 UDP通讯 单通道
def start_UDP_only(can_number=1):

    device_handles = []
    chn_handles = []

    # 打开设备 并不会实际建立连接
    device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_200U_UDP,0,0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
        exit(0)
    print(f"设备句柄: {device_handle}.")

    # 没写则不需要设置
    ret = zcanlib.ZCAN_SetValue(device_handle,"0/local_port", local_port[0].encode("utf-8"))   #设置本地端口
    if ret == ZCAN_STATUS_ERR:
        print("设置本地端口失败！")

    ret = zcanlib.ZCAN_SetValue(device_handle, "0/work_port", work_port[0].encode("utf-8"))  # 设置目标端口
    if ret == ZCAN_STATUS_ERR:
        print("设置目标端口失败！")

    # 配置目标IP，需要与电脑网口保持同一网段
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/ip", "192.168.0.178".encode("utf-8"))
    if ret == ZCAN_STATUS_ERR:
        print("设置目标IP失败！")

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

    for i in range(can_number):
        ret = zcanlib.ZCAN_SetValue(device_handle, str(i) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set CH%d  set_device_tx_echo failed!" % (i))
            return None

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

    # 读取设备信息
    Read_Device_Info(device_handles[0])

    return device_handles, chn_handles

# 同理 UDP通讯 多通道
def start_UDP_all(can_number=1):

    device_handles = []
    chn_handles = []

    for i in range(can_number):
        # 打开设备 并不会实际建立连接
        device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_200U_UDP,i,0)
        if device_handle == INVALID_DEVICE_HANDLE:
            print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
            exit(0)
        print(f"设备句柄: {device_handle}.")

        # 没写则不需要设置
        ret = zcanlib.ZCAN_SetValue(device_handle,"0/local_port", local_port[i].encode("utf-8"))   #设置本地端口
        if ret == ZCAN_STATUS_ERR:
            print("设置本地端口失败！")

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

        ret = zcanlib.ZCAN_SetValue(device_handle, str(i) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set CH%d  set_device_tx_echo failed!" % (i))
            return None

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

    # 读取设备信息
    Read_Device_Info(device_handles[can_number-1])

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

        rcv_canfd_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CANFD)  # CANFD
        if rcv_canfd_num:
            if rcv_num > 100 :
                rcv_canfd_msgs, rcv_canfd_num = zcanlib.ReceiveFD(chn_handle, 100,100)
            else :
                rcv_canfd_msgs, rcv_canfd_num = zcanlib.ReceiveFD(chn_handle, rcv_canfd_num,100)
            with print_lock:
                for msg in rcv_canfd_msgs[:rcv_canfd_num]:
                    frame = msg.frame
                    brs = "加速" if frame.flags & 0x1 else "   "
                    can_type = "CANFD" + brs
                    direction = "TX" if frame.flags & 0x20 else "RX"
                    frame_type = "扩展帧" if frame.can_id & (1 << 31) else "标准帧"
                    frame_format = "远程帧" if frame.can_id & (1 << 30) else "数据帧"     # CANFD没有远程帧
                    can_id = hex(frame.can_id & 0x1FFFFFFF)
                    data = " ".join([f"{num:02X}" for num in frame.data[:frame.len]])

                    print(f"[{msg.timestamp}] CAN{chn_handle & 0xFF} {can_type:<{CANType_width}}\t{direction} ID: {can_id:<{id_width}}\t{frame_type} {frame_format}"
                          f" DLC: {frame.len}\tDATA(hex): {data}")

        rcv_merge_num = zcanlib.GetReceiveNum(device_handle, ZCAN_TYPE_MERGE)  # CANFD
        if rcv_merge_num:
            if rcv_num > 100 :
                rcv_merger_msgs, rcv_merge_num = zcanlib.ReceiveData(device_handle, 100,100)
            else :
                rcv_merger_msgs, rcv_merge_num = zcanlib.ReceiveData(device_handle, rcv_merge_num, 100)
            with print_lock:
                for msg in rcv_merger_msgs[:rcv_merge_num]:
                    if msg.dataType == ZCAN_DT_ZCAN_CAN_CANFD_DATA:
                        flag = msg.zcanfddata.flag
                        frame = msg.zcanfddata.frame
                        type = "CANFD" if flag.frameType else "CAN"
                        brs = "加速" if  (frame.flags & 0x1) else "   "
                        can_type = type + brs
                        direction = "TX" if msg.zcanfddata.flag.txEchoed else "RX"
                        frame_type = "扩展帧" if frame.can_id & (1 << 31) else "标准帧"
                        frame_format = "远程帧" if frame.can_id & (1 << 30) else "数据帧"
                        can_id = frame.can_id & 0x1FFFFFFF
                        data = " ".join([f"{num:02X}" for num in frame.data[:frame.len]])

                        print(f"[{msg.zcanfddata.timestamp}] CAN{msg.chnl} {can_type:<{CANType_width}}\t{direction} ID: {hex(can_id):<{id_width}}\t{frame_type} {frame_format}"
                        f" DLC: {frame.len}\tDATA(hex): {data}")
    print("=====")

# 设置滤波  白名单过滤(只接收范围内的数据)    CANFDNET设备支持设置最多16组滤波   -- 只有CANFDNET-400U支持
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

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_mode", "1".encode("utf-8"))  # 扩展帧滤波
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

# 动态配置
def Dynamic_config_test(device_handle,chn):

    CfgData = ZCAN_DYNAMIC_CONFIG_DATA()

    # 设置(仲裁域)波特率    ---只能填写常见波特率(采样点80%)    特殊波特率需要用自定义波特率设置
    memset(addressof(CfgData), 0, sizeof(ZCAN_DYNAMIC_CONFIG_DATA))
    CfgData.key = ZCAN_DYNAMIC_CONFIG_CAN_NOMINALBAUD(chn).encode("utf-8")  #b"DYNAMIC_CONFIG_CAN0_NOMINALBAUD"#
    CfgData.value = "500000".encode("utf-8")
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/add_dynamic_data", byref(CfgData))
    if ret != ZCAN_STATUS_OK:
        print("设置(仲裁域)波特率失败！")
        return
    else:
        print(f"成功配置 (仲裁域)波特率为 {CfgData.value.decode('utf-8')}")

    # 设置数据域波特率  ---只能填写常见波特率    特殊波特率需要用自定义波特率设置
    memset(addressof(CfgData), 0, sizeof(ZCAN_DYNAMIC_CONFIG_DATA))
    CfgData.key = ZCAN_DYNAMIC_CONFIG_CAN_DATABAUD(chn).encode("utf-8")
    CfgData.value = "2000000".encode("utf-8")
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/add_dynamic_data", byref(CfgData))
    if ret != ZCAN_STATUS_OK:
        print("设置数据域波特率失败！")
        return
    else:
        print(f"成功配置 数据域波特率为 {CfgData.value.decode('utf-8')}")

    # 设置终端电阻    ---0-禁能(关闭)    1-使能(开启)
    memset(addressof(CfgData), 0, sizeof(ZCAN_DYNAMIC_CONFIG_DATA))
    CfgData.key = ZCAN_DYNAMIC_CONFIG_CAN_USERES(chn).encode("utf-8")
    CfgData.value = "1".encode("utf-8")
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/add_dynamic_data", byref(CfgData))
    if ret != ZCAN_STATUS_OK:
        print("设置数据域波特率失败！")
        return
    else:
        print(f"成功配置 终端电阻 {CfgData.value.decode('utf-8')}   0-禁能(关闭) 1-使能(开启)")

    # 设置动态配置效果  ---0下电不保存   1-下电保存，一直生效
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/apply_dynamic_data", "1".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("动态配置失败！")
        return

# 定时发送 设置   每通道最多100条   使能队列发送 的情况下，启动定时发送！！！
def Auto_Send_test(device_handle,chn):

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/clear_auto_send", "0".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Clear CH%d CANFDNET AutoSend failed!" % (chn))
        exit(0)

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

    ret = zcanlib.ZCAN_SetValue(device_handle,str(chn)+"/auto_send",byref(auto_can))
    if ret != ZCAN_STATUS_OK:
        print("设置定时发送 CAN%d 失败!" % chn)
        return None

    auto_canfd = ZCANFD_AUTO_TRANSMIT_OBJ()
    memset(addressof(auto_canfd), 0, sizeof(auto_canfd))
    auto_canfd.index = 1        # 定时发送序列号 用于标记这条报文
    auto_canfd.enable = 1       # 使能该条定时发送 0-关闭 1-启用
    auto_canfd.interval = 500   # 定时周期，单位ms
    auto_canfd.obj.transmit_type = 0  # 0-正常发送，2-自发自收
    auto_canfd.obj.frame.can_id = 0x7F  # ID
    auto_canfd.obj.frame.can_id |= 1 << 31
    auto_canfd.obj.frame.len = 64  # 长度
    # auto_canfd.obj.frame.flags |= 0x20  # 发送回显
    for j in range(auto_canfd.obj.frame.len):
        auto_canfd.obj.frame.data[j] = j

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/auto_send", byref(auto_canfd))
    if ret != ZCAN_STATUS_OK:
        print("设置定时发送 CANFD%d 失败!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/apply_auto_send", "0".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Apply CH%d CANFDNET AutoSend failed!" % (chn))
        return None

# 队列发送示例
def Queue_Transmit_Test(device_handle,chn,chn_handle):

    # 队列发送  设置使能队列发送 0-关闭 1-使能  老卡需要这里使能队列发送+结构体中使能队列发送，新卡只需要执行后者，新卡不需要执行以下操作
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/set_send_mode", "1".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d Queue Mode failed!" % chn)
        # return None

    # 发送 CAN 报文
    transmit_num = 10
    msgs = (ZCAN_Transmit_Data * transmit_num)()
    for i in range(transmit_num):
        msgs[i].transmit_type = 0  # 0-正常发送，2-自发自收
        msgs[i].frame.can_id = 10  # 发送id
        msgs[i].frame.can_id |= 1 << 31  # 最高位(bit31)为 扩展帧/标准帧 标识位 同理 bit30为 数据帧/远程帧
        msgs[i].frame.can_dlc = 8  # 数据长度
        msgs[i].frame._pad |= 0x20  # 发送回显
        msgs[i].frame._pad |= 1<<7      # 设置队列发送
        msgs[i].frame._res0 = 10  # 10ms res0，res1共同表示队列发送间隔(可以理解为一个short 2Byte分开传入)
        msgs[i].frame._res1 = 0
        for j in range(msgs[i].frame.can_dlc):
            msgs[i].frame.data[j] = j

    # 获取队列发送缓存
    ret = zcanlib.ZCAN_GetValue(device_handle, str(chn) + "/get_device_available_tx_count/1")
    count = cast(ret, POINTER(c_int))[0]    # Get_Value 返回值为 void* 需要转换为对应类型 解引用才能正确读出值
    # print("队列缓存%d" % count)
    if count < transmit_num:
        ret = 0
    else:
        ret = zcanlib.Transmit(chn_handle, msgs, transmit_num)
    with print_lock:
        print("队列发送--成功发送 %d 条CAN报文" % ret)

    # 发送 CANFD 报文
    transmit_canfd_num = 10
    canfd_msgs = (ZCAN_TransmitFD_Data * transmit_canfd_num)()
    for i in range(transmit_num):
        canfd_msgs[i].transmit_type = 0  # 0-正常发送，2-自发自收
        canfd_msgs[i].frame.can_id = 0x1ffffff0  # ID
        canfd_msgs[i].frame.can_id |= 1 << 31
        canfd_msgs[i].frame.len = 64  # 长度
        canfd_msgs[i].frame.flags |= 0x20    # 发送回显
        canfd_msgs[i].frame.flags |= 0x1  # BRS 加速标志位：0不加速，1加速
        canfd_msgs[i].frame.flags |= 1 << 7  # 设置队列发送
        canfd_msgs[i].frame._res0 = 0
        canfd_msgs[i].frame._res0 = 1   # 256ms
        for j in range(canfd_msgs[i].frame.len):
            canfd_msgs[i].frame.data[j] = j
    ret = zcanlib.ZCAN_GetValue(device_handle, str(chn) + "/get_device_available_tx_count/1")
    count = cast(ret, POINTER(c_int))[0]
    if count < transmit_num:
        ret = 0
    else:
        ret = zcanlib.TransmitFD(chn_handle, canfd_msgs, transmit_canfd_num)
    with print_lock:
        print("队列发送--成功发送 %d 条CANFD报文" % ret)

# 关闭发送任务 即关闭 队列发送 和 定时发送
def Clear_Send_Task(device_handle,chn):

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/clear_auto_send", "0".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Clear CH%d AutoSend failed!" % (chn))
        exit(0)

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/clear_delay_send_queue", "0".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Clear CH%d QueueSend failed!" % (chn))
        exit(0)

# 发送示例
def Transmit_Test(chn_handle):
    # 发送 CAN 报文
    transmit_num = 10
    msgs = (ZCAN_Transmit_Data * transmit_num)()
    for i in range(transmit_num):
        msgs[i].transmit_type = 0       # 0-正常发送，2-自发自收
        msgs[i].frame.can_id = 10       # 发送id
        msgs[i].frame.can_id |= 1<<31   # 最高位(bit31)为 扩展帧/标准帧 标识位 同理 bit30为 数据帧/远程帧
        # msgs[i].frame.can_id |= 1 << 30
        msgs[i].frame.can_dlc = 8       # 数据长度
        msgs[i].frame._pad |= 0x20      # 发送回显
        # msgs[i].frame._pad |= 1<<7      # 设置队列发送
        msgs[i].frame._res0 = 10        # res0，res1共同表示队列发送间隔(可以理解为一个short 2Byte分开传入)
        msgs[i].frame._res1 = 0
        for j in range(msgs[i].frame.can_dlc):
            msgs[i].frame.data[j] = j
    ret = zcanlib.Transmit(chn_handle, msgs, transmit_num)
    with print_lock: print(f"通道{chn_handle & 0xFF} 成功发送 {ret} 条CAN报文")

    # 发送 CANFD 报文
    transmit_canfd_num = 10
    canfd_msgs = (ZCAN_TransmitFD_Data * transmit_canfd_num)()
    for i in range(transmit_num):
        canfd_msgs[i].transmit_type = 0     # 0-正常发送，2-自发自收
        canfd_msgs[i].frame.can_id = 0x1ffffff0     # ID
        canfd_msgs[i].frame.can_id |= 1 << 31
        # canfd_msgs[i].frame.can_id |= 1 << 30
        canfd_msgs[i].frame.len = 64         # 长度
        canfd_msgs[i].frame.flags |= 0x20    # 发送回显
        canfd_msgs[i].frame.flags |= 0x1    # BRS 加速标志位：0不加速，1加速
        # canfd_msgs[i].frame.flags |= 1 << 7  # 设置队列发送
        canfd_msgs[i].frame._res0 = 10
        for j in range(canfd_msgs[i].frame.len):
            canfd_msgs[i].frame.data[j] = j
    ret = zcanlib.TransmitFD(chn_handle, canfd_msgs, transmit_canfd_num)
    with print_lock:
        print(f"通道{chn_handle & 0xFF} 成功发送 {ret} 条CANFD报文")

# 总线利用率上报线程     --- CANFDNET系列设备 需要在 【网络配置工具】 中使能总线利用率上报，才会生效 代表网络包包头为 55 03
def read_bus_usage(device_handle,chn_index):
    while thread_flag:
        time.sleep(1)
        pBus = zcanlib.ZCAN_GetValue(device_handle,"0/get_bus_usage/1")
        if pBus==None:
            print("Get CH%d BusUsage failed!" %(chn_index))
            continue
        usage= cast(pBus, POINTER(BusUsage))
        new_usage=float(usage.contents.nBusUsage)/100
        print(f"busload: {new_usage:.2f}")

if __name__ == "__main__":
    zcanlib = ZCAN()

    threads = []

    # PC启动一个服务器 供客户端连接
    # handles,chn_handles = start_server_only(2)

    # PC启动多个服务器
    # handles,chn_handles = start_server_all(2)

    # PC作为一个客户端，去连接CANFDNET服务器
    # handles,chn_handles = start_client_only(2)

    # PC作为多个客户端，去连接CANFDNET服务器
    handles, chn_handles = start_client_all(2)

    # UDP通讯，单连接
    # handles, chn_handles = start_UDP_only(2)

    # UDP通讯，多连接
    # handles, chn_handles = start_UDP_all(2)

    # 动态配置
    # time.sleep(3)
    # Dynamic_config_test(handles[0], 0)

    # 滤波    ---CANFDNET-400U才支持
    # Set_Filter(handles[0], 0)

    for i in range(len(handles)):
        # 设置使能合并接收
        ret = zcanlib.ZCAN_SetValue(handles[i], str(0) + "/set_device_recv_merge", repr(enable_merge_receive))
        if ret != ZCAN_STATUS_OK:
            print("Open CH%d recv merge failed!" % i)

    if len(handles) == 1:   #当只打开一次设备 连接所有通道时，可以通过合并接收收取所有通道的数据
        if enable_merge_receive == 1:  # 若开启合并接收，所有通道都由一个接收线程处理
            thread = threading.Thread(target=receive_thread, args=(handles[0], chn_handles[0]))  # 开启独立接收线程
            threads.append(thread)
            thread.start()
        else:  # 若没有开启合并接收，所有通道的数据需要各自开一个线程接收
            for j in range(len(chn_handles)):
                thread = threading.Thread(target=receive_thread, args=(handles[0], chn_handles[j]))  # 开启独立接收线程
                threads.append(thread)
                thread.start()
    else:
        print("============")
        # 当打开多次设备时 无法实现通道合并功能
        for i in range(len(handles)):
            thread = threading.Thread(target=receive_thread, args=(handles[i], chn_handles[i]))  # 开启独立接收线程
            threads.append(thread)
            thread.start()

    if enable_bus_usage == 1:   #   开启总线利用率上报   --- 线程需要填入设备句柄，及通道索引
        thread = threading.Thread(target=read_bus_usage, args=(handles[0],0) )   # 开启独立线程获取通道0的总线利用率
        threads.append(thread)
        thread.start()

    # 发送报文示例
    # Transmit_Test(chn_handles[1])
    # Transmit_Test(chn_handles[0])

    # 定时发送示例
    # Auto_Send_test(handles[0],0)

    # 队列发送示例
    # Queue_Transmit_Test(handles[0],0,chn_handles[0])

    # 合并发送示例
    # Merge_Transmit_Test(handles[0], 0, chn_handles[0])


    # 回车退出
    input()
    thread_flag = False

    # 关闭 定时发送/队列发送 任务
    for i in range(len(handles)):
        Clear_Send_Task(handles[i], 0)

    # 关闭接收线程
    if enable_merge_receive == 1:
        threads[0].join()
    else:
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
            print("关闭设备成功")