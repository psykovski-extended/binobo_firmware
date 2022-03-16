import time
import network
import uwebsockets.client
import urequests
import _thread
import ujson
import os

try:
    import asyncio as uasyncio
except ImportError:
    import uasyncio
from machine import ADC, Pin, Timer


class Multiplexer:
    def __init__(self, pin1, pin2, pin3, pin4, a_readable: ADC, a_channels, enable: Pin):
        """
        Represents the CD74HCT4067 16 Channel Analogue Multiplexer, with 4 digital pins to select the analogue channel
        to be read.

        :param pin1: First digital Pin, should not be null.
        :param pin2: Second digital Pin, can be null.
        :param pin3: Third digital Pin, can be null.
        :param pin4: Fourth digital Pin, can be null.
        :param a_readable: Signal from multiplexed channel
        :param a_channels: amount of analogue channels that are connected to the board
        :param enable: Enable-Pin
        """
        self.pins = []

        if pin1 is not None:
            self.pins.append(pin1)
        if pin2 is not None:
            self.pins.append(pin2)
        if pin3 is not None:
            self.pins.append(pin3)
        if pin4 is not None:
            self.pins.append(pin4)

        self.enable = enable  # inverted
        self.a_readable = a_readable
        self.a_readable.atten(ADC.ATTN_11DB)
        self.a_readable.width(ADC.WIDTH_12BIT)

        self.len_pins = len(self.pins)

        self.a_channels = a_channels

        self.enable_()

    def enable_(self):
        """
        Enables the board
        """
        self.enable.off()

    def disable(self):
        """
        Disables the board
        """
        self.enable.on()

    def map_nibble_to_pins(self, nibble):
        """
        Maps a given 4-bit value to the multiplexer-pins

        :param nibble: Nibble to be mapped --> f.e.: 0b0101 - S3 = 0, S2 = 1, S1 = 0, S0 = 1
        """
        if nibble > 15:
            raise ValueError(
                "There are not more than 16 channels per Multiplexer (0 to 15)! Pared value: " + str(nibble))

        for i in range(self.len_pins):
            self.pins[i].value(nibble & 0b0001)
            nibble >>= 1

    def read_all(self):
        """
        Reads all channels connected to the multiplexer
        :return: Returns an array of the retrieved values, in raw format
        """
        res = []
        for i in range(self.a_channels):
            self.map_nibble_to_pins(i)
            res.append(self.a_readable.read())
        return res

    def read_one(self, ch):
        """
        Reads one channel connected to the multiplexer and returns teh retrieved value
        :param ch: Channel to be read --> channel counting begins at 0!
        :return: returns the retrieved value
        """
        self.map_nibble_to_pins(ch)
        res = self.a_readable.read()
        return res


class ADCIter:
    def __init__(self, *multiplexer: Multiplexer):
        """
        Creates a kind-of Iterator, where the objects itself is iterating over the parsed multiplexers, so there is no
        need to iterate over it with calling iter(obj)
        :param multiplexer: Multiplexer-Objects to iterate over
        """
        self.multiplexer = multiplexer
        self.a_ch = []
        for i in self.multiplexer:
            self.a_ch.append(i.a_channels)

    def retrieve_data_raw(self):
        """
        Reads all analogue channels and converts the result to a 1D-List, where the items are ordered by the order the
        of the parsed multiplexer and chronologically by the number of the pins on the multiplexer board
        :return: 1D-List of the retrieved analogue values
        """
        res = []
        for multi in self.multiplexer:
            for i in multi.read_all():
                res.append(i)

        return res


# data buffer
data = []
# vector, storing zero position
zero_pos = []
# indicates that the iteration for data-retrieving is done or still ongoing
iteration_done = False
# token to identify the connected user
token = ""
# wifi-ssid
ssid = ""
# wifi-password
password = ""
# websocket client object
ws = None
# mechanical degree of freedom
mdof = 340
# factor for data conversion
f_rc = mdof / 4095
# multiplexer 1
multi1 = Multiplexer(Pin(25, Pin.OUT), Pin(33, Pin.OUT), Pin(32, Pin.OUT), Pin(12, Pin.OUT), ADC(Pin(34)), 16,
                     Pin(26, Pin.OUT))
# multiplexer 2
multi2 = Multiplexer(Pin(23, Pin.OUT), Pin(22, Pin.OUT), Pin(21, Pin.OUT), None, ADC(Pin(35)), 6, Pin(5, Pin.OUT))
# ADCIter obj
adc_iter = ADCIter(multi1, multi2)


def connect_to_wifi(ssid="", pw=""):
    """
    Tries to establish a connection to a wifi
    :param ssid: SSID of the wifi to connect to
    :param pw: Password of the wifi to connect to
    :return: returns the wrapper object of the connection
    """
    sta_if = network.WLAN(network.STA_IF)
    sta_if.active(True)

    sta_if.connect(ssid, pw)
    sta_if.config(reconnects=2)

    while not sta_if.isconnected():
        pass

    if not sta_if.isconnected():
        raise Exception("Could not connect to WIFI!")

    return sta_if


def convert_retrieved_data(data_to_conv: list):
    """
    converts the retrieved data from an analogue value to degrees
    :param data_to_conv: Data to be converted
    :return:
    """
    for i in range(22):
        data_to_conv[i] = data_to_conv[i] * f_rc - zero_pos[i]
    return data_to_conv


def retrieve_data():
    """
    retrieves data from all connected multiplexer boards and appends it to the data-buffer
    """
    global iteration_done, data
    iteration_done = False
    data.append(convert_retrieved_data(adc_iter.retrieve_data_raw()))
    iteration_done = True

    if len(data) > 10:
        data = []


async def publish_data():
    """
    Sends the buffered data to the websocket server over a websocket-connection
    """
    global data
    while not iteration_done or len(data) < 3:
        await uasyncio.sleep(0.001)
    temp = data
    data = []
    try:
        ws.send(str([token, temp]))
    except:
        connect_websocket()


async def async_data_publishing():
    """
    Endlessly publishes data over the websocket connection
    """
    while True:
        await publish_data()


async def uart_input_reader():
    """
    Reads commands retrieved over UART0 --> not fully implemented yet!
    """
    while True:
        cmd = input()
        # TODO: interpret command
        print(cmd)


def uart_data_thread_main():
    """
    Thread-routine, publishes data on separate thread to avoid blocking of data retrieving
    This routine should als read uart-data to interpret commands received while runtime, but this feature is not implemented yet
    """
    event_loop = uasyncio.get_event_loop()
    event_loop.create_task(async_data_publishing())
    # event_loop.create_task(uart_input_reader())
    event_loop.run_forever()


def connect_websocket():
    """
    Tries to connect to 'ws://emuesp32.binobo.io' to establish a websocket connection
    """
    global ws
    print("[ESP32]: Connecting to Websocket Server...")
    try:
        ws = uwebsockets.client.connect('ws://emuesp32.binobo.io')  #
        print("[ESP32]: Connections successfully established!")
    except:
        print("[ESP32]: Couldn't connect to Websocket!")


def calibrate():
    """
    Reads all channels to determine where the zero-position of the potentiometers are, needed to measure the positions
    of the fingers
    """
    global zero_pos
    print("[ESP32]: Calibration starts...")
    input("[ESP32]: Zero Position --> Waiting for verification...\n")
    zero_pos = adc_iter.retrieve_data_raw()

    for i in range(len(zero_pos)):
        zero_pos[i] = zero_pos[i] * f_rc

    print("[ESP32]: Calibration done.")


def main():
    global token, ssid, password, ws

    input("Hit <enter> to start configuration...\n")

    print("[ESP32]: Configuration starts...")

    is_storage = "config.txt" in os.listdir()
    use_storage = False

    if is_storage:
        with open("config.txt", "r") as config:
            lines = config.readlines()
            ssid = lines[0][:-1]
            password = lines[1][:-1]
            token = lines[2][:-1]
            print("[1]" + ssid, "[2]" + password, "[3]" + token, sep="\n")
        x = input("Use local stored config data? [y/n]:\n")
        use_storage = x is "y"

    connected = False
    is_connection_error = False
    while not connected:
        if not use_storage or is_connection_error:
            ssid = input("SSID:\n")
            password = input("Password:\n")
        try:
            connect_to_wifi(ssid, password)
            print("[ESP32]: Connection successfully established!")
            connected = True
        except:
            is_connection_error = True
            input("[ESP32]: Error occurred while connecting, please try again.")

    # token_valid = False
    # is_stored_token_valid = True
    # while not token_valid:
    #     if not use_storage or not is_stored_token_valid:
    #         token = input("Token:\n")
    #     print("[ESP32]: Validating token...")
    #     res = urequests.get(url="https://www.binobo.io/roboData/rest_api/validate_token?token=" + str(token))
    #     try:
    #         if ujson.loads(res.text)['status'] == "SUCCESS":
    #             token_valid = True
    #             input("[ESP32]: Token valid.")
    #         else:
    #             is_stored_token_valid = False
    #             input("[ESP32]: Token not valid, try again.")
    #     except KeyError or IndexError:
    #         pass

    if not use_storage:
        token = input("Token:\n")

    calibrate()
    connect_websocket()

    if not use_storage:
        store_data = input("Store configuration data? [y/n]:\n") == "y"
        if store_data:
            with open("config.txt", "w") as config:
                config.write(ssid + "\n" + password + "\n" + token + "\n")
                print("[ESP32]: Config-Data stored!")

    print("[ESP32]: Configuration done! Have fun!")

    timer = Timer(0)
    timer.init(period=33, mode=Timer.PERIODIC, callback=lambda t: retrieve_data())
    _thread.start_new_thread(uart_data_thread_main, ())


if __name__ == "__main__":
    main()
