# coding=utf-8
# device.py
# Author: Meghan Clark
# This file contains a Device object that exposes a high-level API for interacting
# with a LIFX device, and which caches some of the more persistent state attributes
# so that you don't always need to spam the light with packets.
#
# The Device object also provides the low-level workflow functions for sending
# LIFX unicast packets to the specific device. LIFX unicast packets are sent
# via UDP broadcast, but by including the device's MAC other LIFX devices will
# ignore the packet.
#
# Currently service and port are set during initialization and never updated.
# This may need to change in the future to support multiple (service, port) pairs
# per device, and also to capture in real time when a service is down (port = 0).
import netifaces as ni
from contextlib import suppress
from datetime import datetime
from socket import timeout
from time import sleep, time
from typing import NamedTuple, Optional, Dict

from lifxlan3.network.message import BROADCAST_MAC
from lifxlan3.network.msgtypes import Acknowledgement, GetGroup, GetHostFirmware, GetInfo, GetLabel, GetLocation, GetPower,\
    GetVersion, GetWifiFirmware, GetWifiInfo, SERVICE_IDS, SetLabel, SetPower, StateGroup, StateHostFirmware,\
    StateInfo, StateLabel, StateLocation, StatePower, StateVersion, StateWifiFirmware, StateWifiInfo, str_map
from .products import features_map, product_map, light_products
from lifxlan3.settings import UNKNOWN, PowerSettings
from lifxlan3.network.unpack import unpack_lifx_message
from lifxlan3.utils import timer, exhaust, init_socket, WaitPool, init_log

DEFAULT_TIMEOUT = .8  # second
DEFAULT_ATTEMPTS = 4

VERBOSE = False

log = init_log(__name__)


class NoResponse(Exception):
    """raised when no response is recv'd"""


def get_broadcast_addrs():
    broadcast_addrs = []
    for iface in ni.interfaces():
        try:
            ifaddr = ni.ifaddresses(iface)[ni.AF_INET][0]
            if ifaddr['addr'] != '127.0.0.1':
                broadcast_addrs.append(ifaddr['broadcast'])
        except:  # for interfaces that don't support ni.AF_INET
            pass
    return broadcast_addrs


UDP_BROADCAST_IP_ADDRS = get_broadcast_addrs()
UDP_BROADCAST_PORT = 56700


class TimeInfo(NamedTuple):
    time: int
    uptime: int
    downtime: int


class WifiInfo(NamedTuple):
    signal: int
    tx: int
    rx: int


class FirmwareInfo(NamedTuple):
    build_timestamp: int = -1
    version: float = -1.0


class ProductInfo(NamedTuple):
    vendor: str = UNKNOWN
    product: str = UNKNOWN
    version: str = UNKNOWN


class SupportsDesc:
    """return whether or not a certain feature is supported based on the member name"""

    def __set_name__(self, owner, name):
        self.feature = name.split('_', 1)[-1]

    def __get__(self, instance, owner):
        if not instance:
            return self

        instance._refresh_version_info(only_if_needed=True)
        return instance.product_features[self.feature]


class Device(object):
    # mac_addr is a string, with the ":" and everything.
    # service is an integer that maps to a service type. See SERVICE_IDS in msgtypes.py
    # source_id is a number unique to this client, will appear in responses to this client
    def __init__(self, mac_addr, ip_addr, service, port, source_id, verbose=False):
        self.verbose = verbose
        self.mac_addr = mac_addr.lower()
        self.port = port
        self.service = service
        self.source_id = source_id
        self.ip_addr = ip_addr  # IP addresses can change, though...

        self.label = None
        self.location = None
        self.group = None
        self.power_level = None
        self.host_firmware_info = FirmwareInfo()
        self.wifi_firmware_info = FirmwareInfo()
        self.product_info = ProductInfo()

        self._wait_pool = WaitPool(12)

    ###########################################################################
    #                                                                          #
    #                            Device API Methods                            #
    #                                                                          #
    ############################################################################

    def __hash__(self):
        return hash(self.mac_addr)

    def __eq__(self, other):
        return self.mac_addr == other.mac_addr

    def __lt__(self, other):
        return self.group < other.group

    # ==================================================================================================================
    # DEVICE PROPERTIES
    # ==================================================================================================================

    @property
    def product(self):
        return self.product_info.product

    @property
    def product_name(self):
        return product_map.get(self.product)

    @property
    def product_features(self):
        return features_map.get(self.product)

    @property
    def is_light(self) -> bool:
        self._refresh_version_info(only_if_needed=True)
        return self.product in light_products

    supports_color = SupportsDesc()
    supports_temperature = SupportsDesc()
    supports_multizone = SupportsDesc()
    supports_infrared = SupportsDesc()
    supports_chain = SupportsDesc()

    # ==================================================================================================================
    # SETTERS
    # ==================================================================================================================

    def set_label(self, label):
        self.label = label[:32]
        self._send_set_message(SetLabel, dict(label=self.label), rapid=False)

    def set_power(self, power, rapid=False, **payload_kwargs):
        self._set_power(SetPower, power, rapid=rapid, **payload_kwargs)

    def _set_power(self, msg_type, power, rapid=False, **payload_kwargs):
        power = PowerSettings.validate(power)
        ps = PowerSettings.validate(self.power_level)
        if ps is power:
            return
        log.info(f'setting power to {power}: {payload_kwargs}')
        self.power_level = power
        payload = {'power_level': self.power_level, **payload_kwargs}
        self._send_set_message(msg_type, payload, rapid=rapid)

    # ==================================================================================================================
    # REFRESH LOCAL VALUES (grab data from lights and cache)
    # ==================================================================================================================
    @property
    def _refresh_funcs(self):
        return (self._refresh_label,
                self._refresh_location,
                self._refresh_group,
                self._refresh_power,
                self._refresh_host_firmware_info,
                self._refresh_wifi_firmware_info,
                self._refresh_version_info)

    # noinspection PyUnreachableCode
    @timer
    def refresh(self):
        """full refresh for all interesting values"""
        with self._wait_pool as wp:
            exhaust(map(wp.submit, self._refresh_funcs))

        with suppress(NoResponse):
            exhaust(f.result() for f in wp.futures)
            return True

        return False

    def _refresh_label(self):
        response = self.req_with_resp(GetLabel, StateLabel)
        self.label = response.label.encode('utf-8')
        if type(self.label).__name__ == 'bytes':  # Python 3
            self.label = self.label.decode('utf-8')

    def _refresh_location(self):
        response = self.req_with_resp(GetLocation, StateLocation)
        self.location = response.label.encode('utf-8')
        if type(self.location).__name__ == 'bytes':  # Python 3
            self.location = self.location.decode('utf-8')

    def _refresh_group(self):
        response = self.req_with_resp(GetGroup, StateGroup)
        self.group = response.label.encode('utf-8')
        if type(self.group).__name__ == 'bytes':  # Python 3
            self.group = self.group.decode('utf-8')

    def _refresh_power(self):
        response = self.req_with_resp(GetPower, StatePower)
        self.power_level = response.power_level

    def _refresh_host_firmware_info(self):
        response = self.req_with_resp(GetHostFirmware, StateHostFirmware)
        build = response.build
        version = float(str(str(response.version >> 16) + "." + str(response.version & 0xff)))
        self.host_firmware_info = FirmwareInfo(build, version)

    def _refresh_wifi_firmware_info(self):
        response = self.req_with_resp(GetWifiFirmware, StateWifiFirmware)
        build = response.build
        version = float(str(str(response.version >> 16) + "." + str(response.version & 0xff)))
        self.wifi_firmware_info = FirmwareInfo(build, version)

    def _refresh_version_info(self, *, only_if_needed=False):
        if not only_if_needed or (self.product is None or UNKNOWN in self.product_info):
            r = self.req_with_resp(GetVersion, StateVersion)
            self.product_info = ProductInfo(r.vendor, r.product, r.version)

    # ==================================================================================================================
    # GET DATA (grab data from lights but don't cache)
    # ==================================================================================================================

    def _get_wifi_info(self) -> WifiInfo:
        response = self.req_with_resp(GetWifiInfo, StateWifiInfo)
        return WifiInfo(response.signal, response.tx, response.rx)

    def _get_time_info(self) -> TimeInfo:
        response = self.req_with_resp(GetInfo, StateInfo)
        return TimeInfo(response.time, response.uptime, response.downtime)

    ############################################################################
    #                                                                          #
    #                            String Formatting                             #
    #                                                                          #
    ############################################################################

    def device_characteristics_str(self, indent):
        s = "{}\n".format(self.label)
        s += indent + "MAC Address: {}\n".format(self.mac_addr)
        s += indent + "IP Address: {}\n".format(self.ip_addr)
        s += indent + "Port: {}\n".format(self.port)
        s += indent + "Service: {}\n".format(SERVICE_IDS[self.service])
        s += indent + "Power: {}\n".format(str_map(self.power_level))
        s += indent + "Location: {}\n".format(self.location)
        s += indent + "Group: {}\n".format(self.group)
        return s

    def device_firmware_str(self, indent):
        host_build_ns = self.host_firmware_info.build_timestamp
        host_build_s = datetime.utcfromtimestamp(host_build_ns / 1000000000) if host_build_ns is not None else None
        wifi_build_ns = self.wifi_firmware_info.build_timestamp
        wifi_build_s = datetime.utcfromtimestamp(wifi_build_ns / 1000000000) if wifi_build_ns is not None else None
        s = "Host Firmware Build Timestamp: {} ({} UTC)\n".format(host_build_ns, host_build_s)
        s += indent + "Host Firmware Build Version: {}\n".format(self.host_firmware_info.version)
        s += indent + "Wifi Firmware Build Timestamp: {} ({} UTC)\n".format(wifi_build_ns, wifi_build_s)
        s += indent + "Wifi Firmware Build Version: {}\n".format(self.wifi_firmware_info.version)
        return s

    def device_product_str(self, indent):
        s = "Vendor: {}\n".format(self.product_info.vendor)
        s += indent + "Product: {} ({})\n".format(self.product, self.product_name)  #### FIX
        s += indent + "Version: {}\n".format(self.product_info.version)
        s += indent + "Features: {}\n".format(self.product_features)
        return s

    def device_time_str(self, indent):

        nanosec_to_hours = lambda ns: ns / (1000000000.0 * 60 * 60)

        time, uptime, downtime = self._get_time_info()
        time_s = datetime.utcfromtimestamp(time / 1000000000) if time else None
        uptime_s = round(nanosec_to_hours(uptime), 2) if uptime else None
        downtime_s = round(nanosec_to_hours(downtime), 2) if downtime else None
        s = "Current Time: {} ({} UTC)\n".format(time, time_s)
        s += indent + "Uptime (ns): {} ({} hours)\n".format(uptime, uptime_s)
        s += indent + "Last Downtime Duration +/-5s (ns): {} ({} hours)\n".format(downtime, downtime_s)
        return s

    def device_radio_str(self, indent):
        signal, tx, rx = self._get_wifi_info()
        s = "Wifi Signal Strength (mW): {}\n".format(signal)
        s += indent + "Wifi TX (bytes): {}\n".format(tx)
        s += indent + "Wifi RX (bytes): {}\n".format(rx)
        return s

    def __str__(self):
        self.refresh()
        indent = "  "
        s = self.device_characteristics_str(indent)
        s += indent + self.device_firmware_str(indent)
        s += indent + self.device_product_str(indent)
        s += indent + self.device_time_str(indent)
        s += indent + self.device_radio_str(indent)
        return s

    ############################################################################
    #                                                                          #
    #                            Workflow Methods                              #
    #                                                                          #
    ############################################################################

    def _send_set_message(self, msg_type, payload: Optional[Dict] = None, timeout_secs=DEFAULT_TIMEOUT,
                          max_attempts=1, *, rapid: bool):
        """handle sending messages either rapidly or not"""
        args = msg_type, payload, timeout_secs
        if rapid:
            self.fire_and_forget(*args, num_repeats=max_attempts)
        else:
            self.req_with_ack(*args)

    # Don't wait for Acks or Responses, just send the same message repeatedly as fast as possible
    def fire_and_forget(self, msg_type, payload: Optional[Dict] = None, timeout_secs=DEFAULT_TIMEOUT,
                        num_repeats=DEFAULT_ATTEMPTS):
        payload = payload or {}
        with init_socket(timeout_secs) as sock:
            msg = msg_type(self.mac_addr, self.source_id, seq_num=0, payload=payload, ack_requested=False,
                           response_requested=False)
            sent_msg_count = 0
            sleep_interval = 0.05 if num_repeats > 20 else 0
            while sent_msg_count < num_repeats:
                if self.ip_addr:
                    sock.sendto(msg.packed_message, (self.ip_addr, self.port))
                else:
                    for ip_addr in UDP_BROADCAST_IP_ADDRS:
                        sock.sendto(msg.packed_message, (ip_addr, self.port))
                if self.verbose:
                    log.info("SEND: " + str(msg))
                sent_msg_count += 1
                sleep(sleep_interval)  # Max num of messages device can handle is 20 per second.

    # Usually used for Set messages
    def req_with_ack(self, msg_type, payload, timeout_secs=DEFAULT_TIMEOUT, max_attempts=DEFAULT_ATTEMPTS):
        self.req_with_resp(msg_type, Acknowledgement, payload, timeout_secs, max_attempts)

    # Usually used for Get messages, or for state confirmation after Set (hence the optional payload)
    def req_with_resp(self, msg_type, response_type, payload: Optional[Dict] = None, timeout_secs=DEFAULT_TIMEOUT,
                      max_attempts=DEFAULT_ATTEMPTS):
        # Need to put error checking here for arguments
        payload = payload or {}
        if not isinstance(response_type, list):
            response_type = [response_type]
        success = False
        device_response = None
        with init_socket(timeout_secs) as sock:
            ack_requested = len(response_type) == 1 and Acknowledgement in response_type
            msg = msg_type(self.mac_addr, self.source_id, seq_num=0, payload=payload, ack_requested=ack_requested,
                           response_requested=not ack_requested)
            response_seen = False
            attempts = 0
            while not response_seen and attempts < max_attempts:
                sent = False
                start_time = time()
                timedout = False
                while not response_seen and not timedout:
                    if not sent:
                        if self.ip_addr:
                            sock.sendto(msg.packed_message, (self.ip_addr, self.port))
                        else:
                            for ip_addr in UDP_BROADCAST_IP_ADDRS:
                                sock.sendto(msg.packed_message, (ip_addr, self.port))
                        sent = True
                        if self.verbose:
                            log.info("SEND: " + str(msg))
                    try:
                        data, (ip_addr, port) = sock.recvfrom(1024)
                        response = unpack_lifx_message(data)
                        if self.verbose:
                            log.info("RECV: " + str(response))
                        if type(response) in response_type:
                            if response.source_id == self.source_id and (
                                    response.target_addr == self.mac_addr or response.target_addr == BROADCAST_MAC):
                                response_seen = True
                                device_response = response
                                self.ip_addr = ip_addr
                                success = True
                    except timeout:
                        pass
                    elapsed_time = time() - start_time
                    timedout = True if elapsed_time > timeout_secs else False
                attempts += 1
            if not success:
                raise NoResponse(f'WorkflowException: Did not receive {response_type!r} from {self.mac_addr!r} '
                                 f'(Name: {self.label!r}) in response to {msg_type!r}')
            return device_response
