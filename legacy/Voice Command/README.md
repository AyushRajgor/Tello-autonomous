## Voice Command

### Description
This is a simple voice command program that uses the Google Speech Recognition API to recognize speech.

Tello performs task based on the voice command given by the user.

Here, I take two languages as input, English and Gujarati. You can add more languages by yourself. google API supports many languages.


### Dependencies
- Python 3.9+
- djitellopy
- SpeechRecognition
- Pygame
- pyttsx3

### How to run
- Install the dependencies
- Run the program using `python main.py`
- speak the commands
  - take off / land
  - up / down (movement in y-axis)
  - rotate left / rotate right (rotation in z-axis)
  - forward / back (movement in x-axis)
  - left / right (movement in yaw-axis)
  - exit

### controls
- change the threshold value according to your environment
- change the language according to your need