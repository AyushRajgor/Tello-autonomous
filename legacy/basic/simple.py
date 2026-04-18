from djitellopy import Tello
import cv2

tello = Tello()
tello.connect()

tello.streamon()
frame_read = tello.get_frame_read()

tello.takeoff()

movement_speed = 30
rotation_speed = 30

while True:
    img = frame_read.frame
    cv2.imshow("drone", img)

    key = cv2.waitKey(1) & 0xff
    if key == 27:  # ESC key
        break
    elif key == ord('w'):
        tello.move_forward(movement_speed)
    elif key == ord('s'):
        tello.move_back(movement_speed)
    elif key == ord('a'):
        tello.move_left(movement_speed)
    elif key == ord('d'):
        tello.move_right(movement_speed)
    elif key == ord('e'):
        tello.rotate_clockwise(rotation_speed)
    elif key == ord('q'):
        tello.rotate_counter_clockwise(rotation_speed)
    elif key == ord('r'):
        tello.move_up(movement_speed)
    elif key == ord('f'):
        tello.move_down(movement_speed)

tello.land()
tello.streamoff()
tello.end()
