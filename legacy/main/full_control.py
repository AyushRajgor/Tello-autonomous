from djitellopy import Tello
import cv2
import pygame
import numpy as np
import time
import datetime
from multiprocessing import Process
import os
import face_recognition
import pickle
import speech_recognition as sr


protoPath = os.path.join("models\\deploy.prototxt.txt")
modelPath = os.path.join("models\\res10_300x300_ssd_iter_140000.caffemodel")

labelsPath = "models\\coco.names"
weightsPath = 'models\\yolov3.weights'
configPath = 'models\\yolov3.cfg'

file_name = 'models/coco.names'
prototxt = 'MobileNetSSD_deploy.prototxt.txt'
model = 'MobileNetSSD_deploy.caffemodel'

data = pickle.loads(open("models/encodings.pickle", "rb").read())


S = 60  # threshold movement of the drone each time
FPS = 120


S1 = 20  # threshold movement in detection mode
S2 = 5  # alternative threshold movement in detection mode
UDOffset = 150  # offset for up and down
dimensions = (960, 720)  # dimensions of the frame
cWidth = int(dimensions[0] / 2)
cHeight = int(dimensions[1] / 2)


def dt():
    ct = format(datetime.datetime.now())
    ct = ct.split(".")[0].replace(':', '')
    return ct


class Controller(object):
    """
    Combines the Tello and the pygame controller
    Face tracking
    Object detection
    Edge detection
    Face recognition
    """

    def __init__(self):
        """
        Init pygame and key states
        """
        pygame.init()

        pygame.display.set_caption("Tello video stream")
        self.screen = pygame.display.set_mode([960, 720])

        self.tello = Tello()

        self.for_back_velocity = 0
        self.left_right_velocity = 0
        self.up_down_velocity = 0
        self.yaw_velocity = 0
        self.speed = 10

        self.send_rc_control = False

        pygame.time.set_timer(pygame.USEREVENT + 1, 1000 // FPS)

    def yolo(self, frame):
        """
        YOLO object detection
        Computational heavy function use SSD instead
        :param frame: frame from the video stream
        """
        LABELS = open(labelsPath).read().strip().split("\n")
        # initialize a list of colors to represent each possible class label
        np.random.seed(42)
        COLORS = np.random.randint(0, 255, size=(len(LABELS), 3), dtype="uint8")

        print("[INFO] loading YOLO from disk...")
        net = cv2.dnn.readNetFromDarknet(configPath, weightsPath)
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)

        ln = net.getLayerNames()
        ln = [ln[i - 1] for i in net.getUnconnectedOutLayers()]
        # initialize the video stream, pointer to output video file, and
        # frame dimensions
        writer = None
        (W, H) = (None, None)
        # try to determine the total number of frames in the video file
        # read the next frame from the file
        # if the frame was not grabbed, then we have reached the end
        # of the stream
        # if the frame dimensions are empty, grab them
        if W is None or H is None:
            (H, W) = frame.shape[:2]
        # construct a blob from the input frame and then perform a forward
        # pass of the YOLO object detector, giving us our bounding boxes
        # and associated probabilities
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (416, 416),
                                     swapRB=True, crop=False)
        net.setInput(blob)
        start = time.time()
        layerOutputs = net.forward(ln)
        end = time.time()
        # initialize our lists of detected bounding boxes, confidences,
        # and class IDs, respectively
        boxes = []
        confidences = []
        classIDs = []
        # loop over each of the layer outputs
        for output in layerOutputs:
            # loop over each of the detections
            for detection in output:
                # extract the class ID and confidence (i.e., probability)
                # of the current object detection
                scores = detection[5:]
                classID = np.argmax(scores)
                confidence = scores[classID]
                # filter out weak predictions by ensuring the detected
                # probability is greater than the minimum probability
                if confidence > 0.5:
                    # scale the bounding box coordinates back relative to
                    # the size of the image, keeping in mind that YOLO
                    # actually returns the center (x, y)-coordinates of
                    # the bounding box followed by the boxes' width and
                    # height
                    box = detection[0:4] * np.array([W, H, W, H])
                    (centerX, centerY, width, height) = box.astype("int")
                    # use the center (x, y)-coordinates to derive the top
                    # and and left corner of the bounding box
                    x = int(centerX - (width / 2))
                    y = int(centerY - (height / 2))
                    # update our list of bounding box coordinates,
                    # confidences, and class IDs
                    boxes.append([x, y, int(width), int(height)])
                    confidences.append(float(confidence))
                    classIDs.append(classID)
        # apply non-maxima suppression to suppress weak, overlapping
        # bounding boxes
        idxs = cv2.dnn.NMSBoxes(boxes, confidences, 0.5, 0.3)
        # ensure at least one detection exists
        if len(idxs) > 0:
            # loop over the indexes we are keeping
            for i in idxs.flatten():
                # extract the bounding box coordinates
                (x, y) = (boxes[i][0], boxes[i][1])
                (w, h) = (boxes[i][2], boxes[i][3])
                # draw a bounding box rectangle and label on the frame
                color = [int(c) for c in COLORS[classIDs[i]]]
                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                text = "{}: {:.4f}".format(LABELS[classIDs[i]],
                                           confidences[i])
                cv2.putText(frame, text, (x, y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    def ssd(self, frame_read):
        """
        SSD object detection
        Not as accurate as YOLO but faster
        :param frame_read: frame from the video stream
        """
        ct = dt()
        count = 0
        keepRecording = False
        check = False
        while True:

            frame = frame_read.frame
            height, width, _ = frame.shape
            classLabels = ["background", "aeroplane", "bicycle", "bird", "boat",
                           "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
                           "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
                           "sofa", "train", "tvmonitor"]
            # classLabels = fpt.read().rstrip('\n').split('\n')

            COLORS = np.random.uniform(0, 255, size=(len(classLabels), 3))

            net = cv2.dnn.readNetFromCaffe(prototxt, model)
            # initialize the video stream, allow the cammera sensor to warmup,
            # and initialize the FPS counter

            # grab the frame from the threaded video stream and resize it
            # to have a maximum width of 400 pixels
            # frame = vs.read()
            # frame = imutils.resize(frame, width=400)
            # # grab the frame dimensions and convert it to a blob
            (h, w) = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)),
                                         0.007843, (300, 300), 127.5)
            # pass the blob through the network and obtain the detections and
            # predictions
            net.setInput(blob)
            detections = net.forward()
            # loop over the detections
            for i in np.arange(0, detections.shape[2]):
                # extract the confidence (i.e., probability) associated with
                # the prediction
                confidence = detections[0, 0, i, 2]
                # filter out weak detections by ensuring the `confidence` is
                # greater than the minimum confidence
                if confidence > 0.2:
                    # extract the index of the class label from the
                    # `detections`, then compute the (x, y)-coordinates of
                    # the bounding box for the object
                    idx = int(detections[0, 0, i, 1])
                    box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                    (startX, startY, endX, endY) = box.astype("int")
                    # draw the prediction on the frame
                    label = "{}: {:.2f}%".format(classLabels[idx],
                                                 confidence * 100)
                    cv2.rectangle(frame, (startX, startY), (endX, endY),
                                  COLORS[idx], 2)
                    y = startY - 15 if startY - 15 > 15 else startY + 15
                    cv2.putText(frame, label, (startX, y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[idx], 2)

            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_o:
                        return
                    elif event.key == pygame.K_z:  # Take picture
                        ct = dt()
                        cv2.imwrite(f"img\\picture{ct}.png", frame)
                    elif event.key == pygame.K_r:  # Take video
                        check = True
                        if count == 0:
                            start_time = time.time()
                            keepRecording = False
                            count = 1

                        else:
                            check = False
                            video.release()

                    else:
                        self.keydown(event.key)
                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)

            if check:

                if not keepRecording:
                    video = cv2.VideoWriter(f'video\\video{ct}.avi', cv2.VideoWriter_fourcc(*'XVID'), 15,
                                            (width, height))
                    keepRecording = True
                if keepRecording:
                    t = round(time.time() - start_time, 0)
                    minute = int(t / 60)
                    seconds = int(t % 60)
                    times = str(minute) + ':' + str(seconds)
                    cv2.putText(frame, times, (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    cv2.putText(frame, "Recording", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    video.write(frame)
                    time.sleep(1 / 60)

            cv2.putText(frame, "Object Detection Start", (10, 700), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            text = "Battery: {}%".format(self.tello.get_battery())
            cv2.putText(frame, text, (700, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))
            pygame.display.update()

    def videoRecorder(self, frame_read):
        """
        Record video
        :param frame_read: frame from the video stream
        """
        start_time = time.time()
        keepRecording = False
        ct = dt()
        while True:

            img = frame_read.frame
            height, width, _ = img.shape

            if keepRecording:
                t = round(time.time() - start_time, 0)
                minute = int(t / 60)
                seconds = int(t % 60)
                times = str(minute) + ':' + str(seconds)
                cv2.putText(img, times, (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                cv2.putText(img, "Recording", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            if not keepRecording:
                start_time = time.time()
                video = cv2.VideoWriter(f'video\\video{ct}.avi', cv2.VideoWriter_fourcc(*'XVID'), 30,
                                        (width, height))
                keepRecording = True

            if keepRecording:
                video.write(img)
                time.sleep(1 / 60)

            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_z:  # Take picture
                        ct = dt()
                        cv2.imwrite(f"img\\picture{ct}.png", img)

                    if event.key == pygame.K_r:
                        keepRecording = False
                        return
                    else:
                        self.keydown(event.key)

                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)

            text = "Battery: {}%".format(self.tello.get_battery())
            cv2.putText(img, text, (700, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))

            pygame.display.update()

    def edge(self, frame_read):
        """
        Canny Edge detection
        use to detect the edge of the object in the frame
        :param frame_read: frame from the video stream
        black and white frame with white edge
        """
        ct = dt()
        count = 0
        check = False
        keepRecording = False
        while True:
            blurred = cv2.GaussianBlur(frame_read.frame, (5, 5), sigmaX=0)
            edges = cv2.Canny(blurred, 80, 40)
            # edges = cv2.resize(edges, (1280, 720))

            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_c:
                        return
                    elif event.key == pygame.K_z:  # Take picture
                        ct = dt()
                        cv2.imwrite(f"img\\picture{ct}.png", edges)
                    elif event.key == pygame.K_r:  # Take video
                        check = True
                        if count == 0:
                            start_time = time.time()
                            keepRecording = False
                            count = 1

                        else:
                            check = False
                            video.release()

                    else:
                        self.keydown(event.key)
                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)
            if check:

                if not keepRecording:
                    video = cv2.VideoWriter(f'video\\video{ct}_edge.avi', cv2.VideoWriter_fourcc(*'XVID'), 30,
                                            (960, 720),isColor=False)
                    keepRecording = True
                if keepRecording:
                    t = round(time.time() - start_time, 0)
                    minute = int(t / 60)
                    seconds = int(t % 60)
                    times = str(minute) + ':' + str(seconds)
                    cv2.putText(edges, times, (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    cv2.putText(edges, "Recording", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    video.write(edges)
                    time.sleep(1 / 60)

            cv2.putText(edges, "Edge Detection Start", (10, 700), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            text = "Battery: {}%".format(self.tello.get_battery())
            cv2.putText(edges, text, (700, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

            frame = cv2.cvtColor(edges, cv2.COLOR_BGR2RGB)


            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))

            pygame.display.update()

    def tracking(self, frame_read):
        """
        Track the face in the frame and move the drone to the center of the face
        :param frame_read: frame from the video stream
        """
        while True:
            self.update()
            frame = frame_read.frame
            frame = cv2.resize(frame, dimensions)
            detector = cv2.dnn.readNetFromCaffe(protoPath, modelPath)

            height, width = frame.shape[:2]
            imageBlob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), (104.0, 177.0, 123.0))
            detector.setInput(imageBlob)
            detections = detector.forward()
            cv2.circle(frame, (480, 360), 10, (0, 255, 0), 3, cv2.LINE_AA)

            for i in range(0, detections.shape[2]):
                # extract the confidence (i.e., probability) associated with the
                # prediction
                confidence = detections[0, 0, i, 2]
                # filter out weak detections by ensuring the `confidence` is
                # greater than the minimum confidence
                if confidence < 0.5:
                    continue
                else:
                    self.for_back_velocity = 0
                    self.left_right_velocity = 0
                    self.up_down_velocity = 0
                    self.yaw_velocity = 0

                # compute the (x, y)-coordinates of the bounding box for the
                # object
                box = detections[0, 0, i, 3:7] * np.array([width, height, width, height])
                (startX, startY, endX, endY) = box.astype("int")

                # draw the bounding box of the face along with the associated
                # probability
                text = "{:.2f}%".format(confidence * 100)
                y = startY - 10 if startY - 10 > 10 else startY + 10
                cv2.rectangle(frame, (startX, startY), (endX, endY), (0, 0, 255), 2)

                # cv2.putText(frame, text, (startX, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 2)

                y_mid = int((startY + endY) / 2) + UDOffset
                x_mid = int((startX + endX) / 2)
                h = int(endY - startY)
                w = int(endX - startX)
                area = h * w
                y = y_mid - UDOffset
                vtr = np.array((cWidth, cHeight, 22000))
                vtg = np.array((x_mid, y_mid, area))
                vDistance = vtr - vtg
                cv2.circle(frame, (x_mid, y), 10, (0, 255, 255), 3, cv2.LINE_AA)
                cv2.line(frame, (480, 360), (x_mid, y), (255, 0, 0), 4, cv2.LINE_AA)

                # print(f"startX:{startX}, startY:{startY}, endX:{endX}, endY:{endY}")
                # print(f"y_mid:{y_mid}, x_mid:{x_mid}, area:{area}")
                # print(f"vDistance[0]:{vDistance[0]},vDistance[1]:{vDistance[1]},vDistance[2]:{vDistance[2]}")

                if vDistance[0] < -100:
                    self.yaw_velocity = S1
                    # self.left_right_velocity = S2
                elif vDistance[0] > 100:
                    self.yaw_velocity = -S1
                    # self.left_right_velocity = -S2
                else:
                    self.yaw_velocity = 0

                    # for up & down
                if vDistance[1] > 55:
                    self.up_down_velocity = S1
                elif vDistance[1] < -55:
                    self.up_down_velocity = -S1
                else:
                    self.up_down_velocity = 0

                # F = 0
                # if abs(vDistance[2]) > 150:
                #     F = S

                # for forward back
                if 21000 < area < 23000:
                    self.for_back_velocity = 0
                elif area < 20000:
                    self.for_back_velocity = S1
                elif area > 24000:
                    self.for_back_velocity = -S1
                else:
                    self.for_back_velocity = 0

            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_f:
                        return
                    elif event.key == pygame.K_z:  # Take picture
                        ct = dt()
                        cv2.imwrite(f"img\\picture{ct}.png", frame)
            cv2.putText(frame, "Face Detection Start", (10, 10), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            text = "Battery: {}%".format(self.tello.get_battery())
            cv2.putText(frame, text, (700, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))
            pygame.display.update()

    def reco(self, frame_read):
        """
        Face recognition using the face_recognition library
        model HOG is faster but less accurate than CNN
        :param frame_read: frame from the video stream
        """
        while True:
            self.update()
            frame = frame_read.frame
            frame = cv2.resize(frame, dimensions)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            r = frame.shape[1] / float(rgb.shape[1])
            boxes = face_recognition.face_locations(rgb, model="hog")
            encodings = face_recognition.face_encodings(rgb, boxes)
            names = []
            for encoding in encodings:
                # attempt to match each face in the input image to our known
                # encodings
                matches = face_recognition.compare_faces(data["encodings"], encoding)  # return True/False
                # print("matchs: ",matches)
                name = "Unknown"
                # check to see if we have found a match
                if True in matches:
                    # find the indexes of all matched faces then initialize a
                    # dictionary to count the total number of times each face
                    # was matched
                    matchedIdxs = [i for (i, b) in enumerate(matches) if b]  # return list of index of true
                    # print("matchedIdxs: ",matchedIdxs)
                    counts = {}
                    # loop over the matched indexes and maintain a count for
                    # each recognized face face
                    for i in matchedIdxs:
                        name = data["names"][i]  # retrive name from encoding of
                        counts[name] = counts.get(name, 0) + 1
                    # determine the recognized face with the largest number
                    # of votes (note: in the event of an unlikely tie Python
                    # will select first entry in the dictionary)
                    name = max(counts, key=counts.get)

                # update the list of names

                names.append(name)

                # loop over the recognized faces
            for ((top, right, bottom, left), name) in zip(boxes, names):
                # rescale the face coordinates
                top = int(top * r)
                right = int(right * r)
                bottom = int(bottom * r)
                left = int(left * r)
                # draw the predicted face name on the image
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
                y = top - 15 if top - 15 > 15 else top + 15
                cv2.putText(frame, name, (left, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)

            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_g:
                        return
                    elif event.key == pygame.K_z:  # Take picture
                        ct = dt()
                        cv2.imwrite(f"img\\picture{ct}.png", frame)
            cv2.putText(frame, "Face Recognition Start", (10, 10), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))
            pygame.display.update()

    def voice(self, frame_read):
        voice = True

        while True:
            img = frame_read.frame
            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_z:  # Take picture
                        ct = dt()
                        cv2.imwrite(f"img\\picture{ct}.png", img)

                    if event.key == pygame.K_v:
                        voice = False
                        return
                    else:
                        self.keydown(event.key)

                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)

            if voice:
                print(voice)
                r = sr.Recognizer()
                with sr.Microphone() as source:
                    print("Listening....")
                    r.pause_threshold = 1
                    audio = r.listen(source)
                    print("Recognising...")
                    try:
                        query = r.recognize_google(audio, language='en-IN')
                    except Exception as e:
                        print(e)
                        print("---")
                    if query == "take off":
                        print(query)
                        self.tello.takeoff()

            cv2.putText(img, "Voice Control Start", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))

            pygame.display.update()

    def run(self):

        self.tello.connect()
        self.tello.set_speed(self.speed)

        # In case streaming is on. This happens when we quit this program without the escape key.
        self.tello.streamoff()
        self.tello.streamon()

        frame_read = self.tello.get_frame_read()
        should_stop = False
        while not should_stop:
            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.QUIT:
                    should_stop = True
                elif event.type == pygame.KEYDOWN:

                    if event.key == pygame.K_r:  # Take video
                        recorder = Process(target=self.videoRecorder(frame_read))
                        recorder.start()
                        recorder.join()
                    if event.key == pygame.K_c:  # Edge detection
                        recorder = Process(target=self.edge(frame_read))
                        recorder.start()
                        recorder.join()
                    if event.key == pygame.K_f:  # face tracking
                        self.tracking(frame_read)
                    if event.key == pygame.K_o:  # object detection using SSD model
                        recorder = Process(target=self.ssd(frame_read))
                        recorder.start()
                        recorder.join()
                    if event.key == pygame.K_g:  # face recognition
                        self.reco(frame_read)
                    elif event.key == pygame.K_ESCAPE:
                        should_stop = True
                    else:
                        self.keydown(event.key, frame_read)
                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)

            if frame_read.stopped:
                break

            self.screen.fill([0, 0, 0])

            frame = frame_read.frame

            # battery n.
            text = "Battery: {}%".format(self.tello.get_battery())
            cv2.putText(frame, text, (700, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)

            self.screen.blit(frame, (0, 0))

            pygame.display.update()

            time.sleep(1 / FPS)

        # Call it always before finishing. To deallocate resources.
        self.tello.end()

    def keydown(self, key, frame_read=None):
        """ Update velocities based on key pressed Arguments:
            key: pygame key
        """
        if key == pygame.K_UP:  # set forward velocity
            self.for_back_velocity = S
        elif key == pygame.K_DOWN:  # set backward velocity
            self.for_back_velocity = -S
        elif key == pygame.K_LEFT:  # set left velocity
            self.left_right_velocity = -S
        elif key == pygame.K_RIGHT:  # set right velocity
            self.left_right_velocity = S
        elif key == pygame.K_w:  # set up velocity
            self.up_down_velocity = S
        elif key == pygame.K_s:  # set down velocity
            self.up_down_velocity = -S
        elif key == pygame.K_a:  # set yaw counter clockwise velocity
            self.yaw_velocity = -S
        elif key == pygame.K_d:  # set yaw clockwise velocity
            self.yaw_velocity = S
        elif key == pygame.K_z:  # Take picture
            ct = dt()
            cv2.imwrite(f"img\\picture{ct}.png", frame_read.frame)

    def keyup(self, key):
        """ Update velocities based on key released Arguments:
            key: pygame key
        """
        if key == pygame.K_UP or key == pygame.K_DOWN:  # set zero forward/backward velocity
            self.for_back_velocity = 0
        elif key == pygame.K_LEFT or key == pygame.K_RIGHT:  # set zero left/right velocity
            self.left_right_velocity = 0
        elif key == pygame.K_w or key == pygame.K_s:  # set zero up/down velocity
            self.up_down_velocity = 0
        elif key == pygame.K_a or key == pygame.K_d:  # set zero yaw velocity
            self.yaw_velocity = 0

        elif key == pygame.K_t:  # takeoff
            self.tello.takeoff()
            self.send_rc_control = True
        elif key == pygame.K_l:  # land
            not self.tello.land()
            self.send_rc_control = False

    def update(self):
        """ Update routine. Send velocities to Tello."""
        if self.send_rc_control:
            self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity,
                                       self.up_down_velocity, self.yaw_velocity)


if __name__ == '__main__':
    control = Controller()
    control.run()
