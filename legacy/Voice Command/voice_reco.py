import pyttsx3
import speech_recognition as sr

engine = pyttsx3.init()
voices = engine.getProperty('voices')
engine.setProperty('voice', voices[0].id)
voicespeed = 150
engine.setProperty('rate', voicespeed)


def speak(audio):
    engine.say(audio)
    engine.runAndWait()


def ask_lang():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        audio = r.listen(source)
        query = r.recognize_google(audio)
        print(query)
    return query


def takeCommand(query):
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening....")
        r.pause_threshold = 1
        audio = r.listen(source)
    try:
        print("Recognising...")
        if query == 'gujarati':
            query = r.recognize_google(audio, language='gu-IN')
        elif query == 'english':
            query = r.recognize_google(audio, language='en-IN')
    except Exception as e:
        print(e)
        print("---")

        return "None"
    return query


if __name__ == "__main__":

    while True:
        l = []
        lang = ask_lang().lower()
        query = takeCommand(lang).lower()  # take command in query
        print(query)
        l.append(query)
        print(l)
        if "offline" in query:  # quit to end the program
            speak("Which Language you prefer")
            quit()
