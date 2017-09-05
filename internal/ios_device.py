# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Interface for iWptBrowser on iOS devices"""
import base64
import logging
import Queue
import select
import threading
import monotonic

class iOSDevice(object):
    """iOS device interface"""
    def __init__(self, serial=None):
        from .support.ios.usbmux import USBMux
        self.socket = None
        self.serial = serial
        self.must_disconnect = False
        self.mux = USBMux()
        self.message_thread = None
        self.messages = Queue.Queue()
        self.current_id = 0

    def get_devices(self):
        """Get a list of available devices"""
        devices = []
        self.mux.process(0.1)
        if self.mux.devices:
            for device in self.mux.devices:
                devices.append(device.serial)
        return devices

    def is_device_ready(self):
        """Get the battery level and only if it responds and is over 75% is it ok"""
        is_ready = False
        response = self.send_message("battery")
        if response and response > 0.75:
            is_ready = True
        return is_ready

    def connect(self):
        """Connect to the device with the matching serial number"""
        try:
            if self.socket is None:
                self.disconnect()
                self.mux.process(0.1)
                devices = self.mux.devices
                if devices:
                    for device in devices:
                        if self.serial is None or device.serial == self.serial:
                            logging.debug("Connecting to device %s", device.serial)
                            self.must_disconnect = False
                            self.socket = self.mux.connect(device, 19222)
                            self.message_thread = threading.Thread(target=self.pump_messages)
                            self.message_thread.daemon = True
                            self.message_thread.start()
                            break
        except Exception:
            pass
        return self.socket is not None

    def disconnect(self):
        """Disconnect from the device"""
        self.must_disconnect = True
        if self.socket is not None:
            self.socket.close()
        if self.message_thread is not None:
            self.message_thread.join()

    def send_message(self, message, wait=True, timeout=30):
        """Send a command and get the response"""
        response = None
        if self.connect():
            self.current_id += 1
            message_id = self.current_id
            logging.debug(">>> %d:%s", self.current_id, message)
            try:
                self.socket.send("{0:d}:{1}\n".format(self.current_id, message))
                if wait:
                    end = monotonic.monotonic() + timeout
                    while not response and monotonic.monotonic() < end:
                        try:
                            msg = self.messages.get(timeout=1)
                            self.messages.task_done()
                            if msg:
                                if 'id' in msg and msg['id'] == str(message_id):
                                    if msg['msg'] == 'OK':
                                        if 'data' in msg:
                                            response = msg['data']
                                        else:
                                            response = True
                                    else:
                                        response = False
                        except Exception:
                            pass
            except Exception:
                self.disconnect()
        return response

    def flush_messages(self):
        """Flush all of the pending messages"""
        try:
            while True:
                self.messages.get_nowait()
                self.messages.task_done()
        except Exception:
            pass

    def pump_messages(self):
        """Background thread for reading messages from the browser"""
        buff = ""
        while not self.must_disconnect and self.socket != None:
            rlo, _, xlo = select.select([self.socket], [], [self.socket])
            if xlo:
                logging.debug("iWptBrowser disconnected")
                self.messages.put({"msg":"disconnected"})
                return
            if rlo:
                data_in = self.socket.recv(8192)
                if not data_in:
                    logging.debug("iWptBrowser disconnected")
                    self.messages.put({"msg":"disconnected"})
                    return
                buff += data_in
                pos = 0
                while pos >= 0:
                    pos = buff.find("\n")
                    if pos >= 0:
                        message = buff[:pos].strip()
                        buff = buff[pos + 1:]
                        if message:
                            logging.debug("<<< %s", message[:200])
                            parts = message.split("\t")
                            if len(parts) > 1:
                                msg = {'ts': parts[0]}
                                data = None
                                if len(parts) > 2:
                                    data = parts[2]
                                parts = parts[1].split(":")
                                if len(parts) > 1:
                                    msg['id'] = parts[0]
                                    message = parts[1].strip()
                                else:
                                    message = parts[0].strip()
                                if message:
                                    parts = message.split("!")
                                    msg['msg'] = parts[0].strip()
                                    if 'encoded' in parts and data is not None:
                                        data = base64.b64decode(data)
                                    if data is not None:
                                        msg['data'] = data
                                    try:
                                        self.messages.put(msg)
                                    except Exception:
                                        pass
