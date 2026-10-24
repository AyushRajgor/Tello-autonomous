# Main

### Description

This is a combination of different functionalities of computer vision. Using multi-processing I'm able to handle all the tasks simultaneously.
Drone connectivity is handled by djitellopy library.

I combined the functionalities,
- Face Tracking
  - DNN Face Detector (res10_300x300_ssd_iter_140000.caffemodel)
- Object Detection
  - YOLOv3 / SSD MobileNet
- Face Detection
  - face_recognition library
- Edge Detection
  - canny edge detection
- Video Recording
- controlling the drone using keyboard


### Dependencies
- Python 3.9+
- djitellopy
- pygame
- OpenCV
- face_recognition


### Models
You need to download the models and put them in the models folder.
You can easily find the models on web. 
- Face Tracing
  - [res10_300x300_ssd_iter_140000.caffemodel]()
  - [deploy.prototxt.txt]()
- Object Detection (SSD MobileNet)
  - [MobileNetSSD_deploy.caffemodel]()
  - [MobileNetSSD_deploy.prototxt.txt]()
- Object Detection (YOLOv3)
  - [yolov3.weights]()
  - [yolov3.cfg]()
  - [coco.names]()

### How to run
- Install the dependencies
- Run the program using `python full_control.py`

### Controls
- press `t` to take off
- press `w` and `s` to move up and down
- press `a` and `d` to rotate left and right
- press `up` and `down` to move forward and back
- press `left` and `right` to move left and right
- press `l` to land
- press `z` to take a picture

### Note
- You can change the threshold value according to your environment.
- You can add more functionalities like gesture control, hand tracking, etc.
