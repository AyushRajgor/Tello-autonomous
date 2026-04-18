import time
from pygame import mixer

'''
Pre-generated Audio from Amazon AWS Polly
'''


def welcome_audio():
    mixer.init(48000, -16, 1, 1024)
    mixer.music.load("Audio/greetings.mp3")
    mixer.music.play()
    while mixer.music.get_busy():  # wait for music to finish playing
        time.sleep(1)


def movement_audio(audio):
    mixer.init()
    mixer.music.load(f"Audio/{audio}.mp3")
    mixer.music.play()
    while mixer.music.get_busy():  # wait for music to finish playing
        time.sleep(1)


def language(lang):
    mixer.init()
    mixer.music.load(f"Audio/{lang}.mp3")
    mixer.music.play()
    while mixer.music.get_busy():
        time.sleep(1)
