from machine import Pin, PWM, RTC, I2C, Timer
import time
import network
import ntptime
import math
from secrets import SSID, PASSWORD, NTP_SERVER

# Pin definitions for ESP32-C3
CLK_PIN = 6
MOSI_PIN = 7
I2C_SDA_PIN = 4  # IO4 for I2C SDA
I2C_SCL_PIN = 5  # IO5 for I2C SCL

# LE (Latch Enable) pins for each chip (CS0-CS3 are LE pins)
LE_PINS = {
    0: 10,  # CS0 = IO10 = LE0
    1: 3,   # CS1 = IO3  = LE1
    2: 1,   # CS2 = IO1  = LE2
    3: 0,   # CS3 = IO0  = LE3
}
OE_PIN = 9   # Output Enable (PWM signal)

# Light sensor configuration
LTR329_I2C_ADDR = 0x29  # I2C address of LTR-329ALS-01
LTR329_ALS_CONTR = 0x80  # ALS operation mode control
LTR329_ALS_MEAS_RATE = 0x85  # ALS measurement rate and integration time
LTR329_PART_ID = 0x86  # Part number ID and revision ID
LTR329_DATA_CH1_LOW = 0x88  # ALS data CH1 low byte
LTR329_DATA_CH1_HIGH = 0x89  # ALS data CH1 high byte
LTR329_DATA_CH0_LOW = 0x8A  # ALS data CH0 low byte
LTR329_DATA_CH0_HIGH = 0x8B  # ALS data CH0 high byte

# Brightness control parameters
MIN_BRIGHTNESS = 200      # Minimum PWM duty cycle (0-1023)
MAX_BRIGHTNESS = 1000     # Maximum PWM duty cycle (0-1023)
LIGHT_SENSOR_UPDATE_MS = 1000  # How often to read light sensor (ms)
LIGHT_SENSOR_GAIN = 1.0  # Adjusts sensitivity (higher = more sensitive)
LIGHT_SENSOR_OFFSET = 0  # Light level offset
LIGHT_SENSOR_GAMMA = 0.5  # Gamma correction (1.0 = linear, <1.0 = more responsive at low light)
BRIGHTNESS_LOG_K = 30.0

# Display constants
CHIP_MAP = {1: 3, 2: 2, 3: 1, 4: 0}  # Spreadsheet chip IDs (1-4) -> output_state indices (0-3)
SECONDS_PER_BLOCK = 7200   # 2 hours in seconds
MAX_DISPLAY_VALUE = 9999   # Maximum value shown on a cistercian digit

# Initialize pins
clk = Pin(CLK_PIN, Pin.OUT)
mosi = Pin(MOSI_PIN, Pin.OUT)
clk.value(0)
mosi.value(0)

# Initialize LE pins as outputs
le = {}
for chip_id, pin in LE_PINS.items():
    le[chip_id] = Pin(pin, Pin.OUT)
    le[chip_id].value(0)  # LE starts low

# Initialize I2C for light sensor
i2c = I2C(0, scl=Pin(I2C_SCL_PIN), sda=Pin(I2C_SDA_PIN), freq=400000)

def init_light_sensor():
    """Initialize the LTR-329ALS-01 light sensor"""
    try:
        # Check if device is present
        devices = i2c.scan()
        if LTR329_I2C_ADDR not in devices:
            print(f"LTR-329ALS-01 not found at 0x{LTR329_I2C_ADDR:02X}")
            return False
            
        # Read part ID (should be 0xA0 for LTR-329)
        part_id = i2c.readfrom_mem(LTR329_I2C_ADDR, LTR329_PART_ID, 1)[0]
        if (part_id & 0xF0) != 0xA0:  # Check manufacturer ID (0xA0 for Lite-On)
            print(f"Unexpected part ID: 0x{part_id:02X}")
            return False
            
        # Configure sensor
        # ALS mode active (0x01), gain 1x (0x00)
        i2c.writeto_mem(LTR329_I2C_ADDR, LTR329_ALS_CONTR, bytes([0x01]))
        # Measurement rate 500ms, integration time 100ms
        i2c.writeto_mem(LTR329_I2C_ADDR, LTR329_ALS_MEAS_RATE, bytes([0x01]))
        
        print("LTR-329ALS-01 initialized successfully")
        return True
    except Exception as e:
        print(f"Error initializing light sensor: {e}")
        return False

def read_light_level():
    """Read light level from LTR-329ALS-01"""
    try:
        # Read both channels (CH1 = IR+visible, CH0 = visible)
        ch0_low = i2c.readfrom_mem(LTR329_I2C_ADDR, LTR329_DATA_CH0_LOW, 1)[0]
        ch0_high = i2c.readfrom_mem(LTR329_I2C_ADDR, LTR329_DATA_CH0_HIGH, 1)[0]
        ch1_low = i2c.readfrom_mem(LTR329_I2C_ADDR, LTR329_DATA_CH1_LOW, 1)[0]
        ch1_high = i2c.readfrom_mem(LTR329_I2C_ADDR, LTR329_DATA_CH1_HIGH, 1)[0]
        
        # Combine high and low bytes
        ch0 = (ch0_high << 8) | ch0_low
        ch1 = (ch1_high << 8) | ch1_low
        
        # Calculate lux (simplified formula - adjust based on your needs)
        ratio = ch1 / (ch0 + ch1) if (ch0 + ch1) > 0 else 0
        
        if ratio < 0.45:
            lux = (1.7743 * ch0 + 1.1059 * ch1) / 6.0
        elif ratio < 0.64:
            lux = (4.2785 * ch0 - 1.9548 * ch1) / 6.0
        elif ratio < 0.85:
            lux = (0.5926 * ch0 + 0.1185 * ch1) / 6.0
        else:
            lux = 0
            
        return max(0, lux)
    except Exception as e:
        print(f"Error reading light sensor: {e}")
        return 0

# Initialize PWM for Output Enable (OE) on IO9
oe_pwm = PWM(Pin(OE_PIN))
oe_pwm.freq(1000)  # 1kHz PWM frequency
oe_pwm.duty(MAX_BRIGHTNESS)  # Start at max brightness

# Global for current brightness
current_brightness = MAX_BRIGHTNESS

# Store the state of all outputs (4 chips × 16 outputs each = 64 bits)
output_state = [0x0000, 0x0000, 0x0000, 0x0000]  # 16-bit register per chip

def shift_in_data(data, num_bits=16):
    """
    Shift data into the shift register chain (MSB first, D15 to D0)
    Data is clocked in on the rising edge of CLK
    data: integer value to shift in
    num_bits: number of bits to shift (16 per chip × 4 = 64 for all chips)
    """
    for i in range(num_bits - 1, -1, -1):  # MSB first (D15 first)
        # Set data line (must be stable before rising edge of CLK)
        mosi.value((data >> i) & 1)
        time.sleep_us(1)
        
        # Clock pulse (data clocked in on rising edge)
        clk.value(1)
        time.sleep_us(1)
        clk.value(0)
        time.sleep_us(1)

def latch_data(chip_id):
    """
    Latch data into a specific chip's output register by pulsing its LE pin.
    LE is transparent when HIGH (outputs follow shift register).
    Data latches on the FALLING edge of LE.
    """
    le[chip_id].value(1)  # Make latch transparent
    time.sleep_us(1)
    le[chip_id].value(0)  # Falling edge - data latches into outputs
    time.sleep_us(1)

def shift_in_all_chips():
    """
    Shift in all 4 chips' data at once (daisy chain).
    Sends all 64 bits: D15-D0 for each chip (MSB first).
    Data propagates through daisy chain via DOUT to DIN.
    """
    combined_data = 0
    # Build 64-bit value from all 4 chips
    # Chip 0 data << 48 | Chip 1 data << 32 | Chip 2 data << 16 | Chip 3 data
    for i in range(4):
        combined_data = (combined_data << 16) | output_state[i]
    
    # Shift in MSB first (16 bits per chip × 4 = 64 bits total)
    shift_in_data(combined_data, 64)

def turn_on_all_leds():
    """
    Turn on all LEDs by setting all output states to 0xFFFF.
    """
    for i in range(4):
        output_state[i] = 0xFFFF
    update_all_chips()

def apply_log_curve(t):
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    # MicroPython doesn't always provide math.log1p
    denom = math.log(1.0 + BRIGHTNESS_LOG_K)
    if denom == 0:
        return t
    return math.log(1.0 + (BRIGHTNESS_LOG_K * t)) / denom

_BREATHE_STEPS = 150  # steps per sine cycle (3000ms / 20ms)

def _breathe_tick(_):
    global _breathe_step, current_brightness
    phase = (2 * math.pi) * (_breathe_step / _BREATHE_STEPS)
    brightness_factor = (math.sin(phase) + 1) / 2
    curved = apply_log_curve(brightness_factor)
    current_brightness = int(MIN_BRIGHTNESS + (MAX_BRIGHTNESS - MIN_BRIGHTNESS) * curved)
    oe_pwm.duty(current_brightness)
    _breathe_step = (_breathe_step + 1) % _BREATHE_STEPS

def start_breathe_timer(step_ms=20):
    global breathe_timer, _breathe_step
    _breathe_step = 0
    breathe_timer = Timer(0)
    breathe_timer.init(mode=Timer.PERIODIC, period=step_ms, callback=_breathe_tick)

def stop_breathe_timer():
    global breathe_timer
    if breathe_timer:
        breathe_timer.deinit()
        breathe_timer = None

def update_all_chips():
    """
    Latch data into all 4 chips.
    Shift in all 64 bits once, then latch each chip individually.
    """
    shift_in_all_chips()
    # Latch each chip in sequence
    for chip_id in range(4):
        latch_data(chip_id)
        time.sleep_us(1)




# LED mapping for digits - extracted from the spreadsheet
# Maps number to list of (chip_id, output_pin) tuples
led_mapping_seconds = {
    0: [(4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    1: [(2, 0), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    2: [(2, 9), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    3: [(2, 11), (2, 5), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    4: [(2, 7), (2, 2), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    5: [(2, 7), (2, 2), (2, 0), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    6: [(4, 1), (2, 4), (2, 1), (4, 0), (2, 8), (3, 3)],
    7: [(2, 0), (4, 1), (2, 4), (2, 1), (4, 0), (2, 8), (3, 3)],
    8: [(2, 9), (4, 1), (2, 4), (2, 1), (4, 0), (2, 8), (3, 3)],
    9: [(2, 9), (2, 0), (4, 1), (2, 4), (2, 1), (4, 0), (2, 8), (3, 3)],
    10: [(2, 15), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    20: [(2, 10), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    30: [(2, 3), (2, 12), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    40: [(2, 6), (2, 14), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    50: [(2, 6), (2, 14), (2, 15), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    60: [(2, 13), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    70: [(2, 15), (2, 13), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    80: [(2, 13), (2, 10), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    90: [(2, 15), (2, 13), (2, 10), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    100: [(4, 7), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    200: [(4, 2), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    300: [(4, 5), (4, 1), (4, 3), (2, 4), (4, 0), (2, 8), (3, 3)],
    400: [(4, 6), (4, 4), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    500: [(4, 7), (4, 6), (4, 4), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    600: [(4, 8), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    700: [(4, 8), (4, 7), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    800: [(4, 8), (4, 2), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    900: [(4, 8), (4, 7), (4, 2), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    1000: [(4, 9), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    2000: [(4, 15), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    3000: [(4, 10), (4, 1), (2, 4), (4, 13), (4, 0), (2, 8), (3, 3)],
    4000: [(4, 11), (4, 1), (2, 4), (4, 0), (4, 12), (2, 8), (3, 3)],
    5000: [(4, 9), (4, 11), (4, 1), (2, 4), (4, 0), (4, 12), (2, 8), (3, 3)],
    6000: [(4, 14), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    7000: [(4, 9), (4, 14), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    8000: [(4, 15), (4, 14), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
    9000: [(4, 9), (4, 15), (4, 14), (4, 1), (2, 4), (4, 0), (2, 8), (3, 3)],
}

led_mapping_2hours = {
    0: [(3, 8), (1, 9), (1, 6)],
    1: [(3, 8), (1, 9), (1, 6),(1,15)],
    2: [(3, 8), (1, 9), (1, 6), (1, 7)],
    3: [(3, 8), (1, 9), (1, 1), (1, 5), (1, 6)],
    4: [(3, 8), (1, 2), (1, 9), (1, 4), (1, 6)],
    5: [(3, 8), (1, 2), (1, 0), (1, 9), (1, 4), (1, 6)],
    6: [(3, 8), (1, 3), (1, 9), (1, 6)],
    7: [(3, 8), (1, 3), (1, 0), (1, 9), (1, 6)],
    8: [(3, 8), (1, 3), (1, 9), (1, 6), (1, 7)],
    9: [(3, 8), (1, 3), (1, 0), (1, 9), (1, 6), (1, 7)],
    10: [(3, 8), (1, 15), (1, 9), (1, 6)],
    20: [(3, 8), (1, 9), (1, 8), (1, 6)],
    30: [(3, 8), (1, 10), (1, 14), (1, 9), (1, 6)],
    40: [(3, 8), (1, 13), (1, 9), (1, 6), (1, 12)],
    50: [(3, 8), (1, 13), (1, 15), (1, 9), (1, 6), (1, 12)],
    60: [(3, 8), (1, 11), (1, 9), (1, 6)],
    70: [(3, 8), (1, 11), (1, 15), (1, 9), (1, 6)],
    80: [(3, 8), (1, 11), (1, 9), (1, 8), (1, 6)],
    90: [(3, 8), (1, 11), (1, 15), (1, 9), (1, 8), (1, 6)],
    100: [(3, 8), (3, 4), (1, 9), (1, 6)],
    200: [(3, 8), (1, 9), (3, 0), (1, 6)],
    300: [(3, 8), (1, 9), (3, 6), (3, 1), (1, 6)],
    400: [(3, 8), (1, 9), (3, 5), (3, 2), (1, 6)],
    500: [(3, 8), (3, 4), (1, 9), (3, 5), (3, 2), (1, 6)],
    600: [(3, 7), (3, 8), (1, 9), (1, 6)],
    700: [(3, 7), (3, 8), (3, 4), (1, 9), (1, 6)],
    800: [(3, 7), (3, 8), (1, 9), (3, 0), (1, 6)],
    900: [(3, 7), (3, 8), (3, 4), (1, 9), (3, 0), (1, 6)],
    1000: [(3, 8), (3, 10), (1, 9), (1, 6)],
    2000: [(3, 8), (3, 15), (1, 9), (1, 6)],
    3000: [(3, 8), (3, 14), (1, 9), (3, 12), (1, 6)],
    4000: [(3, 8), (3, 9), (3, 13), (1, 9), (1, 6)],
    5000: [(3, 8), (3, 9), (3, 10), (3, 13), (1, 9), (1, 6)],
    6000: [(3, 8), (3, 11), (1, 9), (1, 6)],
    7000: [(3, 8), (3, 10), (3, 11), (1, 9), (1, 6)],
    8000: [(3, 8), (3, 15), (3, 11), (1, 9), (1, 6)],
    9000: [(3, 8), (3, 15), (3, 10), (3, 11), (1, 9), (1, 6)],
}

def build_combined_registers(number, mapping):
    """
    Build four 16-bit register values for a number in range 0..9999 by ORing
    the mapping entries for the thousands, hundreds, tens and ones places.

    Returns a list of four integers [reg0, reg1, reg2, reg3] suitable for
    writing into `output_state` (indices 0..3).
    """
    if not isinstance(number, int) or number < 0 or number > MAX_DISPLAY_VALUE:
        raise ValueError(f"number must be an integer between 0 and {MAX_DISPLAY_VALUE}")

    regs = [0x0000, 0x0000, 0x0000, 0x0000]

    for place in (1, 10, 100, 1000):
        digit = (number // place) % 10
        key = digit * place
        for chip_id, output_pin in mapping.get(key, []):
            mapped = CHIP_MAP.get(chip_id)
            if mapped is None or not (0 <= output_pin < 16):
                continue
            regs[mapped] |= (1 << output_pin)

    return regs



def connect_to_wifi(ssid, password, timeout=10, retries=3):
    """
    Connect ESP32-C3 to WiFi network

    Parameters:
    - ssid: WiFi network name
    - password: WiFi password
    - timeout: Maximum time to wait for connection in seconds

    Returns:
    - True if connected successfully
    - False if connection failed
    """
    wlan = network.WLAN(network.STA_IF)
    for attempt in range(retries):
        try:
            wlan.active(False)
            time.sleep_ms(200)
            wlan.active(True)
            try:
                wlan.disconnect()
            except Exception:
                pass
            time.sleep_ms(200)

            print(f"Connecting to WiFi: {ssid}")
            wlan.connect(ssid, password)

            start_time = time.time()
            while not wlan.isconnected():
                if time.time() - start_time > timeout:
                    raise OSError("WiFi connection timeout")
                time.sleep(0.5)
                print(".", end="")

            print()
            print("WiFi connected!")
            print(f"IP Address: {wlan.ifconfig()[0]}")
            return True

        except OSError as e:
            print()
            print(f"WiFi attempt {attempt + 1}/{retries} failed: {e}")
            try:
                wlan.active(False)
            except Exception:
                pass
            time.sleep(1)

    return False

def sync_time_with_ntp(ntp_server="pool.ntp.org", timezone_offset=0):
    """
    Synchronize ESP32-C3 RTC with NTP server
    
    Parameters:
    - ntp_server: NTP server address (default: pool.ntp.org)
    - timezone_offset: Timezone offset from UTC in hours (0 for UTC)
    
    Returns:
    - True if sync successful
    - False if sync failed
    """
    servers = ntp_server
    if isinstance(servers, str):
        servers = [servers]
    elif not isinstance(servers, (list, tuple)):
        servers = ["pool.ntp.org"]

    for server in servers:
        for attempt in range(3):
            try:
                print(f"Syncing time with NTP server: {server}")
                ntptime.server = server
                ntptime.settime()

                rtc = RTC()

                # Apply timezone offset if specified
                if timezone_offset != 0:
                    current_time = list(rtc.datetime())
                    # RTC format: (year, month, day, weekday, hour, minute, second, subsecond)
                    # Use mktime/gmtime to correctly handle day rollover at midnight
                    epoch = time.mktime((current_time[0], current_time[1], current_time[2],
                                        current_time[4], current_time[5], current_time[6],
                                        current_time[3], 0))
                    epoch += int(timezone_offset) * 3600
                    t = time.gmtime(epoch)
                    # gmtime returns (year, month, day, hour, minute, second, weekday, yearday)
                    current_time[0] = t[0]  # year
                    current_time[1] = t[1]  # month
                    current_time[2] = t[2]  # day
                    current_time[3] = t[6]  # weekday
                    current_time[4] = t[3]  # hour
                    current_time[5] = t[4]  # minute
                    current_time[6] = t[5]  # second
                    rtc.datetime(tuple(current_time))

                print("Time synced successfully!")
                print(f"Current time: {rtc.datetime()}")
                return True

            except Exception as e:
                print(f"NTP sync failed (attempt {attempt + 1}/3): {e}")
                time.sleep(1)

    return False

def startup_sync_rtc(ssid=SSID, password=PASSWORD, ntp_server=NTP_SERVER, timezone_offset=0):
    """
    Complete startup routine: connect to WiFi and synchronize RTC with NTP
    
    Parameters:
    - ssid: WiFi network name
    - password: WiFi password
    - ntp_server: NTP server address
    - timezone_offset: Timezone offset from UTC in hours
    
    Returns:
    - True if both WiFi and NTP sync successful
    - False if either step failed
    """
    print("\n========== Starting RTC Synchronization ==========")

    turn_on_all_leds()
    start_breathe_timer()  # Runs independently of blocking WiFi/NTP calls

    # Step 1: Connect to WiFi
    if not connect_to_wifi(ssid, password):
        stop_breathe_timer()
        print("Failed to connect to WiFi. RTC not synchronized.")
        return False

    # Step 2: Sync time with NTP
    time.sleep(1)  # Brief delay after WiFi connection
    if not sync_time_with_ntp(ntp_server, timezone_offset):
        stop_breathe_timer()
        print("Failed to synchronize with NTP. RTC not updated.")
        return False

    stop_breathe_timer()
    print("========== RTC Synchronization Complete ==========\n")
    return True

def get_day_of_year(year, month, day):
    """
    Calculate day of year (1-366)
    
    Parameters:
    - year: Full year (e.g., 2024, 2025)
    - month: Month (1-12)
    - day: Day of month (1-31)
    
    Returns:
    - Day of year (1-366)
    """
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    
    # Check for leap year
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[1] = 29
    
    # Sum days from previous months and add current day
    day_of_year = sum(days_in_month[:month-1]) + day
    return day_of_year

def update_display_from_rtc():
    """
    Update both displays based on current RTC time.
    Called once per second via timer.
    
    Display mapping:
    - First digit: Shows seconds within current 2-hour block (0-7199 mapped to 0-9999)
    - Second digit: Shows which 2-hour block we're in since start of year
    
    This allows you to know your exact position in the year to the second:
    - The second digit tells you which 2-hour block
    - The first digit tells you exactly where in that block
    
    Example: If we're at day 100, hour 5, minute 30, second 45:
    - Block number = (99*24 + 5) // 2 = 1196
    - Seconds in block = (5*3600 + 30*60 + 45) % 7200 = some value
    - First digit displays that scaled value
    - Second digit displays 1196
    """
    try:
        rtc = RTC()
        rtc_time = rtc.datetime()
        
        # RTC datetime format: (year, month, day, weekday, hour, minute, second, subsecond)
        year = rtc_time[0]
        month = rtc_time[1]
        day = rtc_time[2]
        hour = rtc_time[4]
        minute = rtc_time[5]
        second = rtc_time[6]
        
        # Calculate day of year
        day_of_year = get_day_of_year(year, month, day)
        
        # Calculate total seconds elapsed in the year
        seconds_in_year = (day_of_year - 1) * 86400 + hour * 3600 + minute * 60 + second
        
        # Calculate 2-hour block number and seconds within current block
        block_number = seconds_in_year // SECONDS_PER_BLOCK
        seconds_in_block = seconds_in_year % SECONDS_PER_BLOCK

        # Map seconds in block (0..SECONDS_PER_BLOCK-1) to display value (0..MAX_DISPLAY_VALUE)
        first_digit_value = int((seconds_in_block / SECONDS_PER_BLOCK) * MAX_DISPLAY_VALUE)

        # Second digit is the block number
        second_digit_value = block_number

        # Build registers for both digits and combine them
        regs1 = build_combined_registers(first_digit_value, led_mapping_seconds)
        regs2 = build_combined_registers(second_digit_value, led_mapping_2hours)
        
        # OR the registers to display both simultaneously
        combined = [(regs1[i] | regs2[i]) & 0xFFFF for i in range(4)]
        for i in range(4):
            output_state[i] = combined[i]
        
        update_all_chips()

    except Exception as e:
        print(f"Error updating RTC display: {e}")

rtc_timer = None
brightness_timer = None
breathe_timer = None
_breathe_step = 0

def start_rtc_display_timer():
    """
    Start a timer that updates the display every second based on RTC time.
    The display shows your position in the year to the second.
    """
    global rtc_timer
    try:
        # Use Timer(0) to update every 1000ms (1 second)
        rtc_timer = Timer(0)
        rtc_timer.init(mode=Timer.PERIODIC, period=1000, callback=lambda t: update_display_from_rtc())
        print("RTC display timer started - updating every second")
        return True
    except Exception as e:
        print(f"Failed to start RTC display timer: {e}")
        return False

def stop_rtc_display_timer():
    """Stop the RTC display timer."""
    global rtc_timer
    try:
        if rtc_timer:
            rtc_timer.deinit()
            rtc_timer = None
            print("RTC display timer stopped")
            return True
    except Exception as e:
        print(f"Failed to stop RTC display timer: {e}")
        return False

def update_brightness(timer=None):
    """Update display brightness based on ambient light level"""
    global current_brightness
    
    try:
        # Read light level (0-1000+ lux)
        lux = read_light_level()
        
        # Apply offset and gain
        adjusted_lux = max(0, (lux + LIGHT_SENSOR_OFFSET) * LIGHT_SENSOR_GAIN)
        
        normalized = min(1.0, adjusted_lux / 1000.0)
        gamma_corrected = normalized ** LIGHT_SENSOR_GAMMA
        curved = apply_log_curve(gamma_corrected)
        new_brightness = int(MAX_BRIGHTNESS - (MAX_BRIGHTNESS - MIN_BRIGHTNESS) * curved)
        
        # Smooth transition (optional)
        if abs(new_brightness - current_brightness) > 5:  # Only update if significant change
            current_brightness = new_brightness
            oe_pwm.duty(current_brightness)
            
        #print(f"Light: {lux:.1f} lux, computed={new_brightness}, pwm_duty={oe_pwm.duty()}")
        
    except Exception as e:
        print(f"Error in brightness update: {e}")

DEBUG_MAPPING_MODE = False  # Set True to step through LED mappings for validation

def debug_step_mappings(delay_ms=2000):
    """Step through every key in both LED mappings, displaying each for delay_ms ms."""
    oe_pwm.duty(MIN_BRIGHTNESS)  # LEDs visible
    keys = sorted(set(led_mapping_seconds.keys()) | set(led_mapping_2hours.keys()))
    for key in keys:
        for i in range(4):
            output_state[i] = 0x0000
        for chip_id, output_pin in led_mapping_seconds.get(key, []):
            mapped = CHIP_MAP.get(chip_id)
            if mapped is not None and 0 <= output_pin < 16:
                output_state[mapped] |= (1 << output_pin)
        for chip_id, output_pin in led_mapping_2hours.get(key, []):
            mapped = CHIP_MAP.get(chip_id)
            if mapped is not None and 0 <= output_pin < 16:
                output_state[mapped] |= (1 << output_pin)
        update_all_chips()
        print(f"Mapping key: {key}")
        time.sleep_ms(delay_ms)

# Test the setup
if __name__ == "__main__":
    if DEBUG_MAPPING_MODE:
        debug_step_mappings()
        import sys
        sys.exit()
    turn_on_all_leds()

    light_sensor_initialized = init_light_sensor()
    
    # Synchronize RTC with NTP on startup
    # Update SSID and PASSWORD in secrets.py before running!
    time_synced = startup_sync_rtc(
        ssid=SSID,
        password=PASSWORD,
        ntp_server=NTP_SERVER,
        timezone_offset=0  # Change to your timezone offset (e.g., -5 for EST, 1 for CET)
    )
    
    # Only start auto-brightness after successful time sync
    if time_synced:
        # Start auto-brightness if sensor is initialized
        if light_sensor_initialized:
            brightness_timer = Timer(1)
            brightness_timer.init(period=LIGHT_SENSOR_UPDATE_MS, mode=Timer.PERIODIC, callback=update_brightness)
            print("Auto-brightness enabled after time sync")
        else:
            print("Auto-brightness disabled - using maximum brightness")
            oe_pwm.duty(MAX_BRIGHTNESS)
        
        time.sleep(1)
        
        # Start the RTC display timer - updates display every second
        # First digit: seconds within current 2-hour block (0-9999)
        # Second digit: number of 2-hour blocks elapsed since start of year
        start_rtc_display_timer()
    else:
        print("Time sync failed - auto-brightness will not be started")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        stop_rtc_display_timer()
        print("Done!")