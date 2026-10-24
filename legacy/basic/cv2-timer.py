import time
import cv2

# define a video capture object
vid = cv2.VideoCapture(0)
start_time = time.time()

while True:

    # Capture the video frame
    # by frame
    ret, frame = vid.read()
    frame = cv2.resize(frame, (940, 720))
    t = round(time.time() - start_time, 0)
    minute = int(t / 60)
    seconds = int(t % 60)
    times = str(minute) + ':' + str(seconds)
    cv2.putText(frame, times, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    # gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # blurred = cv2.GaussianBlur(frame, (5, 5),sigmaX=0)
    # edges = cv2.Canny(blurred, 60, 50)
    cv2.circle(frame, (480, 360), 10, (0, 255, 0), 3, cv2.LINE_AA)

    # Display the resulting frame
    cv2.imshow('frame', frame)

    # the 'q' button is set as the
    # quitting button you may use any
    # desired button of your choice
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# After the loop release the cap object
vid.release()
# Destroy all the windows
cv2.destroyAllWindows()
