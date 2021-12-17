import network
import uwebsockets.client
import urequests
import _thread
import ujson

try:
    import asyncio as uasyncio
except ImportError:
    import uasyncio
from machine import ADC, Pin, Timer


class Multiplexer:
    def __init__(self, pin1, pin2, pin3, pin4, a_readable: ADC, a_channels, enable: Pin):
        """
        Represents an 16 Channel Analogue Multiplexer, with 4 digital pins to select the analogue channel to be read.

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
        self.multiplexer = multiplexer
        self.a_ch = []
        for i in self.multiplexer:
            self.a_ch.append(i.a_channels)

    def retrieve_data_raw(self):
        res = []
        for multi in self.multiplexer:
            for i in multi.read_all():
                res.append(i)

        return res


# data buffer
data = []
# vector, storing zero position
zero_pos = []
# vector, storing end position
end_pos = []
factor_pos = []
# data-retrieve iteration done?
iteration_done = False
# data busy?
data_busy = False
token = ""
ssid = ""
password = ""
ws = None
# multiplexer 1
multi1 = Multiplexer(Pin(25, Pin.OUT), Pin(33, Pin.OUT), Pin(32, Pin.OUT), Pin(12, Pin.OUT), ADC(Pin(34)), 16,
                     Pin(26, Pin.OUT))
# multiplexer 2
multi2 = Multiplexer(Pin(23, Pin.OUT), Pin(22, Pin.OUT), Pin(21, Pin.OUT), None, ADC(Pin(35)), 6, Pin(5, Pin.OUT))
# ADCIter obj
adc_iter = ADCIter(multi1, multi2)


def connect_to_wifi(ssid="", pw=""):
    print("[ESP32]: Connecting to WIFI with SSID: " + ssid)
    sta_if = network.WLAN(network.STA_IF)
    sta_if.active(True)

    sta_if.connect(ssid, pw)

    print("[ESP32]: Waiting for connection to be established...")
    while not sta_if.isconnected():
        pass

    if not sta_if.isconnected():
        raise Exception("Could not connect to WIFI!")

    return sta_if


def convert_retrieved_data(data_to_conv: list):
    res = []
    for i in range(22):
        res.append((data_to_conv[i] - zero_pos[0]) * factor_pos[i])
    return res


def retrieve_data():
    global iteration_done, data
    iteration_done = False
    data.append(convert_retrieved_data(adc_iter.retrieve_data_raw()))
    iteration_done = True

    if len(data) > 60:
        data = []


async def publish_data():
    global data_busy, data
    while not iteration_done or len(data) < 2:
        await uasyncio.sleep(0.005)
    temp = data
    data = []
    try:
        ws.send(str([token, temp]))
    except:
        connect_websocket()


async def main_async():
    while True:
        await publish_data()


def uart_input_reader():
    while True:
        cmd = input()  # SE SHIT WORKS
        # TODO: interpret command
        print(cmd)


def connect_websocket():
    global ws
    ws = uwebsockets.client.connect('ws://10.117.170.219:8080')


def calibrate():
    global zero_pos, end_pos
    print("[ESP32]: Calibration starts...")
    input("[ESP32]: Zero Position --> Waiting for verification...")
    zero_pos = adc_iter.retrieve_data_raw()
    input("[ESP32]: End Position --> Waiting for verification...")
    end_pos = adc_iter.retrieve_data_raw()

    for i in range(len(zero_pos)):
        factor_pos.append(90 / (end_pos[i] - zero_pos[i] + 1))

    print(str(zero_pos))
    print(str(end_pos))
    print("[ESP32]: Calibration done.")


def main():
    global token, ssid, password, ws

    print("[ESP32]: Configuration starts...")

    connected = False
    while not connected:
        ssid = input("SSID:")
        password = input("Password:")
        try:
            connect_to_wifi(ssid, password)
            print("[ESP32]: Connection successfully established!")
            connected = True
        except:
            print("[ESP32]: Error occurred while connecting, please try again.")

    token_valid = False
    while not token_valid:
        token = input("Token:\n")
        print("[ESP32]: Validating token...")
        res = urequests.get(url="http://10.117.170.219:1443/roboData/rest_api/validate_token?token=" + token)
        try:
            if ujson.loads(res.text)['status'] == "SUCCESS":
                token_valid = True
                print("[ESP32]: Token valid.")
            else:
                print("[ESP32]: Token not valid, try again.")
        except KeyError or IndexError:
            pass

    calibrate()
    connect_websocket()

    timer = Timer(0)
    timer.init(period=33, mode=Timer.PERIODIC, callback=lambda t: retrieve_data())
    event_loop = uasyncio.get_event_loop()
    event_loop.create_task(main_async())
    _thread.start_new_thread(uart_input_reader, ())
    event_loop.run_forever()


if __name__ == "__main__":
    main()
