#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: LoRa Infrastructure Test
# Description: Validates the gr-mcp runtime pipeline (Docker + XML-RPC)
#              without requiring SDR hardware.
# GNU Radio version: 3.10.12.0

from gnuradio import analog, blocks, gr
from xmlrpc.server import SimpleXMLRPCServer
import signal
import sys
import threading
import time


class lora_infra_test(gr.top_block):
    """Minimal flowgraph for testing gr-mcp runtime infrastructure.

    Signal chain: sig_source → throttle → null_sink
    Variables exposed via XML-RPC: samp_rate, center_freq, lora_sf, lora_bw
    """

    def __init__(self):
        gr.top_block.__init__(self, "LoRa Infrastructure Test", catch_exceptions=True)

        ##################################################
        # Variables (same as LoRa receiver for API compatibility)
        ##################################################
        self.samp_rate = samp_rate = 1000000
        self.center_freq = center_freq = 915e6
        self.lora_sf = lora_sf = 7
        self.lora_bw = lora_bw = 125000

        ##################################################
        # Blocks
        ##################################################
        self.analog_sig_source_0 = analog.sig_source_c(
            samp_rate, analog.GR_COS_WAVE, 1000, 1, 0, 0
        )
        self.blocks_throttle_0 = blocks.throttle(gr.sizeof_gr_complex, samp_rate, True)
        self.blocks_null_sink_0 = blocks.null_sink(gr.sizeof_gr_complex)

        # XML-RPC server for runtime variable control
        self.xmlrpc_server_0 = SimpleXMLRPCServer(("0.0.0.0", 8080), allow_none=True)
        self.xmlrpc_server_0.register_introspection_functions()
        self.xmlrpc_server_0.register_instance(self)
        self.xmlrpc_server_0_thread = threading.Thread(target=self.xmlrpc_server_0.serve_forever)
        self.xmlrpc_server_0_thread.daemon = True
        self.xmlrpc_server_0_thread.start()

        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_sig_source_0, 0), (self.blocks_throttle_0, 0))
        self.connect((self.blocks_throttle_0, 0), (self.blocks_null_sink_0, 0))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.analog_sig_source_0.set_sampling_freq(self.samp_rate)
        self.blocks_throttle_0.set_sample_rate(self.samp_rate)

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, center_freq):
        self.center_freq = center_freq

    def get_lora_sf(self):
        return self.lora_sf

    def set_lora_sf(self, lora_sf):
        self.lora_sf = lora_sf

    def get_lora_bw(self):
        return self.lora_bw

    def set_lora_bw(self, lora_bw):
        self.lora_bw = lora_bw


def main(top_block_cls=lora_infra_test, options=None):
    tb = top_block_cls()
    tb.start()

    print("Flowgraph started, XML-RPC on 0.0.0.0:8080", flush=True)
    print(f"Variables: samp_rate={tb.samp_rate}, center_freq={tb.center_freq}, "
          f"lora_sf={tb.lora_sf}, lora_bw={tb.lora_bw}", flush=True)

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    # Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    tb.stop()
    tb.wait()


if __name__ == "__main__":
    main()
