#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: LoRa SDR Receiver (MCP-built)
# Author: gr-mcp
# Description: LoRa receiver built entirely through MCP tools. Matches Heltec V3 beacon: 915MHz, SF7, BW125k.
# GNU Radio version: 3.10.12.0

from gnuradio import blocks, gr
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from xmlrpc.server import SimpleXMLRPCServer
import threading
import gnuradio.lora_sdr as lora_sdr
import osmosdr
import time




class lora_rx_mcp(gr.top_block):

    def __init__(self):
        gr.top_block.__init__(self, "LoRa SDR Receiver (MCP-built)", catch_exceptions=True)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.samp_rate = samp_rate = 1000000
        self.lora_sf = lora_sf = 7
        self.lora_bw = lora_bw = 125000
        self.center_freq = center_freq = 915e6

        ##################################################
        # Blocks
        ##################################################

        self.xmlrpc_server_0 = SimpleXMLRPCServer(('0.0.0.0', 8080), allow_none=True)
        self.xmlrpc_server_0.register_instance(self)
        self.xmlrpc_server_0_thread = threading.Thread(target=self.xmlrpc_server_0.serve_forever)
        self.xmlrpc_server_0_thread.daemon = True
        self.xmlrpc_server_0_thread.start()
        self.osmosdr_source_0 = osmosdr.source(
            args="numchan=" + str(1) + " " + 'rtl=0'
        )
        self.osmosdr_source_0.set_time_unknown_pps(osmosdr.time_spec_t())
        self.osmosdr_source_0.set_sample_rate(samp_rate)
        self.osmosdr_source_0.set_center_freq(center_freq, 0)
        self.osmosdr_source_0.set_freq_corr(0, 0)
        self.osmosdr_source_0.set_dc_offset_mode(2, 0)
        self.osmosdr_source_0.set_iq_balance_mode(2, 0)
        self.osmosdr_source_0.set_gain_mode(False, 0)
        self.osmosdr_source_0.set_gain(40, 0)
        self.osmosdr_source_0.set_if_gain(20, 0)
        self.osmosdr_source_0.set_bb_gain(20, 0)
        self.osmosdr_source_0.set_antenna('', 0)
        self.osmosdr_source_0.set_bandwidth(0, 0)
        self.low_pass_filter_0 = filter.fir_filter_ccf(
            2,
            firdes.low_pass(
                1,
                samp_rate,
                200e3,
                50e3,
                window.WIN_HAMMING,
                6.76))
        self.lora_rx_0 = lora_sdr.lora_sdr_lora_rx( bw=lora_bw, cr=1, has_crc=True, impl_head=False, pay_len=255, samp_rate=(int(samp_rate/2)), sf=lora_sf, sync_word=[0x12], soft_decoding=True, ldro_mode=2, print_rx=[True,True])
        self.blocks_message_debug_0 = blocks.message_debug(True)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.lora_rx_0, 'out'), (self.blocks_message_debug_0, 'print'))
        self.connect((self.low_pass_filter_0, 0), (self.lora_rx_0, 0))
        self.connect((self.osmosdr_source_0, 0), (self.low_pass_filter_0, 0))


    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.osmosdr_source_0.set_sample_rate(self.samp_rate)
        self.low_pass_filter_0.set_taps(firdes.low_pass(1, self.samp_rate, 200e3, 50e3, window.WIN_HAMMING, 6.76))

    def get_lora_sf(self):
        return self.lora_sf

    def set_lora_sf(self, lora_sf):
        self.lora_sf = lora_sf

    def get_lora_bw(self):
        return self.lora_bw

    def set_lora_bw(self, lora_bw):
        self.lora_bw = lora_bw

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, center_freq):
        self.center_freq = center_freq
        self.osmosdr_source_0.set_center_freq(self.center_freq, 0)




def main(top_block_cls=lora_rx_mcp, options=None):
    tb = top_block_cls()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    tb.start()
    tb.flowgraph_started.set()

    try:
        input('Press Enter to quit: ')
    except EOFError:
        # No stdin (Docker detached mode) — block on signal instead
        try:
            signal.pause()
        except AttributeError:
            # signal.pause() not available on Windows
            import time
            while True:
                time.sleep(1)
    tb.stop()
    tb.wait()


if __name__ == '__main__':
    main()
