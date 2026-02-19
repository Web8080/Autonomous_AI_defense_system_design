"""
ROS placeholder: config and topic names for future ROS integration.
Set ROS_MASTER_URI when running with real ROS stack. No connection here.
"""
import os

ROS_MASTER_URI = os.getenv("ROS_MASTER_URI", "")
ROS_TOPIC_CMD_VEL = os.getenv("ROS_TOPIC_CMD_VEL", "/cmd_vel")
ROS_TOPIC_TELEMETRY = os.getenv("ROS_TOPIC_TELEMETRY", "/telemetry")
