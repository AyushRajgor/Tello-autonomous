from djitellopy import Tello
from voice_reco import *
from speak_polly import *

tello = Tello()
tello.connect()

count = 0

welcome_audio()

movement_throttle = 30
rotation_throttle = 90


def language_call():
    language("lang_ask")  # ask for language
    lang = ask_lang().lower()  # listen for language

    if lang == 'gujarati':  # language confirmation audio
        language("guj_set")
    elif lang == 'english':
        language("eng_set")

    return lang


lang = language_call()

while True:
    string = takeCommand(lang).lower()

    if string == "change the language" or string == "લેંગ્વેજ બદલવી છે":
        lang = language_call()

    if string == "take off" or string == "શરૂ થા":
        print(string)
        tello.takeoff()
    if string == "land" or string == "લેન્ડ કર":
        print(string)
        tello.land()

    if string == "up" or string == "ઉપર જા":
        print(string)
        tello.move_up(movement_throttle)

    if string == "down" or string == "નીચે જા":
        print(string)
        tello.move_down(movement_throttle)

    if string == "rotate left" or string == "ડાબી બાજુ":
        print(string)
        tello.rotate_counter_clockwise(rotation_throttle)

    if string == "rotate right" or string == "જમણી બાજુ":
        print(string)
        tello.rotate_clockwise(rotation_throttle)

    if string == "forward" or string == "આગળ આવ":
        print(string)
        tello.move_forward(movement_throttle)

    if string == "back" or string == "પાછળ જા":
        print(string)
        tello.move_back(movement_throttle)

    if string == "left" or string == "ડાબી સાઈડ":
        print(string)
        tello.move_left(movement_throttle)
    if string == "right" or string == "જમણી સાઈડ":
        print(string)
        tello.move_right(movement_throttle)

    if string == "exit" or string == "ચાલો":
        tello.land()
        break
