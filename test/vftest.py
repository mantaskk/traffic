#! /usr/bin/env python

# standard modules
import logging
import os
import subprocess
import sys
import time

sys.path.insert(1, '../binding')
from core import ost_pb, DroneProxy
from rpc import RpcError
from protocols.mac_pb2 import mac
from protocols.ip4_pb2 import ip4, Ip4
from protocols.ip6_pb2 import ip6, Ip6

class Test:
    pass

class TestSuite:
    def __init__(self):
        self.results = []
        self.total = 0
        self.passed = 0
        self.completed = False

    def test_begin(self, name):
        test = Test()
        test.name = name
        test.passed = False
        self.running = test
        print('-----------------------------------------------------------')
        print('@@TEST: %s' % name)
        print('-----------------------------------------------------------')

    def test_end(self, result):
        if self.running:
            self.running.passed = result
            self.results.append(self.running)
            self.total = self.total + 1
            if result:
                self.passed = self.passed + 1
            self.running = None
            print('@@RESULT: %s' % ('PASS' if result else 'FAIL'))
        else:
            raise Exception('Test end without a test begin')

    def report(self):
        print('===========================================================')
        print('TEST REPORT')
        print('===========================================================')
        for test in self.results:
            print('%s: %d' % (test.name, test.passed))
        print('Passed: %d/%d' % (self.passed, self.total))
        print('Completed: %d' % (self.completed))

    def complete(self):
        self.completed = True

    def passed(self):
        return passed == total and self.completed

# initialize defaults
host_name = '127.0.0.1'
tx_port_number = -1
rx_port_number = -1 
drone_version = ['0', '0', '0']

if sys.platform == 'win32':
    tshark = r'C:\Program Files\Wireshark\tshark.exe'
else:
    tshark = 'tshark'
    

# setup logging
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

print('')
print('This test uses the following topology -')
print('')
print(' +-------+           ')
print(' |       |Tx--->----+')
print(' | Drone |          |')
print(' |       |Rx---<----+')
print(' +-------+           ')
print('')
print('A loopback port is used as both the Tx and Rx ports')
print('')

suite = TestSuite()
drone = DroneProxy(host_name)

try:
    # ----------------------------------------------------------------- #
    # Baseline Configuration for subsequent testcases
    # ----------------------------------------------------------------- #

    # connect to drone
    log.info('connecting to drone(%s:%d)' 
            % (drone.hostName(), drone.portNumber()))
    drone.connect()

    # retreive port id list
    log.info('retreiving port list')
    port_id_list = drone.getPortIdList()

    # retreive port config list
    log.info('retreiving port config for all ports')
    port_config_list = drone.getPortConfig(port_id_list)

    if len(port_config_list.port) == 0:
        log.warning('drone has no ports!')
        sys.exit(1)

    # iterate port list to find a loopback port to use as the tx/rx port id 
    print('Port List')
    print('---------')
    for port in port_config_list.port:
        print('%d.%s (%s)' % (port.port_id.id, port.name, port.description))
        # use a loopback port as default tx/rx port 
        if ('lo' in port.name or 'loopback' in port.description.lower()):
            tx_port_number = port.port_id.id
            rx_port_number = port.port_id.id

    if tx_port_number < 0 or rx_port_number < 0:
        log.warning('loopback port not found')
        sys.exit(1)

    print('Using port %d as tx/rx port(s)' % tx_port_number)

    tx_port = ost_pb.PortIdList()
    tx_port.port_id.add().id = tx_port_number;

    rx_port = ost_pb.PortIdList()
    rx_port.port_id.add().id = rx_port_number;

    # ----------------------------------------------------------------- #
    # TESTCASE: Verify counter16 variable field not affecting any
    #           checksums - vlan-id
    # ----------------------------------------------------------------- #

    # add a stream
    stream_id = ost_pb.StreamIdList()
    stream_id.port_id.CopyFrom(tx_port.port_id[0])
    stream_id.stream_id.add().id = 1
    log.info('adding tx_stream %d' % stream_id.stream_id[0].id)
    drone.addStream(stream_id)

    # configure the stream
    stream_cfg = ost_pb.StreamConfigList()
    stream_cfg.port_id.CopyFrom(tx_port.port_id[0])
    s = stream_cfg.stream.add()
    s.stream_id.id = stream_id.stream_id[0].id
    s.core.is_enabled = True
    s.core.frame_len = 128
    s.control.num_packets = 10

    # setup stream protocols as mac:vlan:eth2:ip4:udp:payload
    p = s.protocol.add()
    p.protocol_id.id = ost_pb.Protocol.kMacFieldNumber
    p.Extensions[mac].dst_mac = 0x001122334455
    p.Extensions[mac].src_mac = 0x00aabbccddee

    p = s.protocol.add()
    p.protocol_id.id = ost_pb.Protocol.kVlanFieldNumber
    vf = p.variable_fields.add()
    vf.type = ost_pb.VariableField.kCounter16
    vf.offset = 2
    vf.mask = 0x0fff
    vf.value = 101
    vf.mode = ost_pb.VariableField.kDecrement
    vf.count = 7

    p = s.protocol.add()
    p.protocol_id.id = ost_pb.Protocol.kEth2FieldNumber

    p = s.protocol.add()
    p.protocol_id.id = ost_pb.Protocol.kIp4FieldNumber
    #reduce typing by creating a shorter reference to p.Extensions[ip4]
    ip = p.Extensions[ip4]
    ip.src_ip = 0x01020304
    ip.dst_ip = 0x05060708

#    p = s.protocol.add()
#    p.protocol_id.id = ost_pb.Protocol.kIp6FieldNumber
#    ip = p.Extensions[ip6]
#    ip.src_addr_hi = 0x2002000000000000
#    ip.src_addr_lo = 0x1001
#    ip.dst_addr_hi = 0x4004000000000000
#    ip.dst_addr_lo = 0x1001

    vf = p.variable_fields.add()
    vf.type = ost_pb.VariableField.kCounter32
    vf.offset = 16 # dst-ip4
    #vf.offset = 20 # src-ip6
    vf.mask = 0x000000ff
    vf.value = 101
    vf.mode = ost_pb.VariableField.kIncrement
    vf.count = 5

    s.protocol.add().protocol_id.id = ost_pb.Protocol.kUdpFieldNumber
    s.protocol.add().protocol_id.id = ost_pb.Protocol.kPayloadFieldNumber

    log.info('configuring tx_stream %d' % stream_id.stream_id[0].id)
    drone.modifyStream(stream_cfg)

    # clear tx/rx stats
    log.info('clearing tx/rx stats')
    drone.clearStats(tx_port)
    drone.clearStats(rx_port)

    passed = False
    suite.test_begin('counter16NotAffectingCksums')
    try:
        drone.startCapture(rx_port)
        drone.startTransmit(tx_port)
        log.info('waiting for transmit to finish ...')
        time.sleep(12)
        drone.stopTransmit(tx_port)
        drone.stopCapture(rx_port)

        log.info('getting Rx capture buffer')
        buff = drone.getCaptureBuffer(rx_port.port_id[0])
        drone.saveCaptureBuffer(buff, 'capture.pcap')
        log.info('dumping Rx capture buffer')
        cap_pkts = subprocess.check_output([tshark, '-r', 'capture.pcap'])
        print(cap_pkts)
        if '5.6.7.8' in cap_pkts and '5.6.7.17' in cap_pkts:
            passed = True
        os.remove('capture.pcap')
    except RpcError as e:
            raise
    finally:
        drone.stopTransmit(tx_port)
        suite.test_end(passed)

    suite.complete()

    # delete streams
    log.info('deleting tx_stream %d' % stream_id.stream_id[0].id)
    drone.deleteStream(stream_id)

    # bye for now
    drone.disconnect()

except Exception as ex:
    log.exception(ex)

finally:
    suite.report()
    if not suite.passed:
        sys.exit(2);
