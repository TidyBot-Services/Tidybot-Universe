from robot_sdk import arm
import time

def main():
    print("Moving arm forward 10cm...")
    arm.move_delta(dx=0.1, frame="base")
    time.sleep(1)
    print("Moving arm backward 10cm...")
    arm.move_delta(dx=-0.1, frame="base")
    print("Done!")

if __name__ == "__main__":
    main()