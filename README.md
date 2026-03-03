# Cistercian Clock

![github](https://github.com/user-attachments/assets/9cd29f81-9203-4d00-91ab-066bb83d9574)

I keep a list of project ideas I want to do, and fairly near the top it says "NEW CLOCK".

After reading about [Cistercian Numerals](https://en.wikipedia.org/wiki/Cistercian_numerals) I decided to make a clock which:

1) Shows the date and time in the least amount of digits
2) Acts as a way of training my brain to do repetitive maths (something I don't enjoy)

The design is a custom PCB based on a ESP32 and MAX6969 LED drivers in a 3D printed housing

# How to read the time

The Cistercian Numeral system uses one digit to be able to count up to 9999

![cistercian](https://github.com/user-attachments/assets/e4d96275-dfc1-48ae-9558-1d56f32a0f27)

The clock has two digits, left and right. The goal was to use as much of the range of each digit as possible.

The right hand digit is counting up in seconds until it reaches 7200 which is 2 hours.

The left hand giti is counting up in 2 hour increments, 4380 sets of 2 hours in a year.

# Software Setup

To setup the ESP32, load MicroPython from https://micropython.org/download/

Once loaded, put the `main.py` and `secrets.py` into the MicroPython environment.

You will need to update the `secrets.py` file with your WiFi details

You can change your timezone in the main.py using timezone_offset

# Mechanical Build

To assemble the clock first put the button into the rear housing

<img width="842" height="595" alt="1_button_in" src="https://github.com/user-attachments/assets/d2c16063-397d-480b-a1e8-7cee21601b09" />

Then insert the PCB into the rear housing

<img width="842" height="595" alt="2_pcb_in" src="https://github.com/user-attachments/assets/8c8cd346-18d9-495c-a6f6-451f53d804d1" />

Insert the light guide and diffuser

<img width="842" height="595" alt="3_front_panels" src="https://github.com/user-attachments/assets/a6eb7dae-0c17-49a0-ac08-1ad3aa21ce69" />

Attach with M2 screws

<img width="842" height="595" alt="4_screws" src="https://github.com/user-attachments/assets/2f19beb2-e1d7-46d9-a878-7834d54a9bcf" />


# PCB Build

The PCB is a simple double-sided board. The Gerbers are in the relevant folder and should be easy to produce by any PCB vendor.



