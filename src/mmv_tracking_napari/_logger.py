import sys
import time

from qtpy.QtWidgets import QMessageBox
from napari.qt.threading import thread_worker


def setup_logging():
    """
    Sets up for print to write to the log file
    """
    plugin_directory = __file__.removesuffix("/src/mmv_tracking_napari/_logger.py")
    path = "{}/hitl4trk.log".format(plugin_directory)
    file = open(path, "w")
    sys.stdout = file
    sys.stderr = file
    print("Logging initialized")


def notify(text):
    """
    Shows a notification dialog

    Parameters
    ----------
    text : str
        The text displayed as the notification
    """
    msg = QMessageBox()
    msg.setWindowTitle("napari")
    msg.setText(text)
    print("Notifying user: '{}'".format(text))
    msg.exec()


@thread_worker
def notify_with_delay(text):
    time.sleep(0.2)
    notify(text)


def choice_dialog(text, choices):
    """
    Shows a dialog where the user has to make a decision

    Parameters
    ----------
    text : str
        The text displayed as the prompt for the decision
    choices : list of tuple or types of buttons
        Tuples of the potential choices, consisting of ("button text", "button type") or button types
    """
    msg = QMessageBox()
    msg.setWindowTitle("napari")
    msg.setText(text)
    for choice in choices:
        if type(choice) is tuple:
            msg.addButton(choice[0], choice[1])
        else:
            msg.addButton(choice)
    print("Prompting user: '{}'".format(text))
    return msg.exec()
