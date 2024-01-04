import multiprocessing
import os
import platform
from multiprocessing import Pool

import napari
import numpy as np
from cellpose import models
from napari.qt.threading import thread_worker
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from scipy import ndimage, optimize, spatial

from ._logger import notify, choice_dialog, handle_exception
from ._grabber import grab_layer
from ._logger import choice_dialog, notify


class ProcessingWindow(QWidget):
    """
    A (QWidget) window to run processing steps on the data. Contains segmentation and tracking.

    Attributes
    ----------
    viewer : Viewer
        The Napari viewer instance

    Methods
    -------
    run_segmentation()
        Run segmentation on the raw image data
    run_demo_segmentation()
        Run the segmentation on the first 5 layers only
    run_tracking()
        Run tracking on the segmented cells
    adjust_ids()
        Replaces track ID 0 & adjusts segmentation IDs to match track IDs
    """

    dock = None  # ?? ich vermute, kluge Menschen wissen, was das hier macht. Braucht keinen Kommentar, aber interessieren würde es mich trotzdem

    def __init__(self, parent):
        """
        Parameters
        ----------
        viewer : Viewer
            The Napari viewer instance
        parent : QWidget
            The parent widget
        """
        super().__init__()
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setLayout(QVBoxLayout())
        self.setWindowTitle("Data processing")
        self.parent = parent
        self.viewer = parent.viewer
        ProcessingWindow.dock = self
        self.choice_event = Event()
        try:
            self.setStyleSheet(napari.qt.get_stylesheet(theme="dark"))
        except TypeError:
            pass

        ### QObjects
        # Labels
        label_segmentation = QLabel("Segmentation")
        label_tracking = QLabel("Tracking")

        # Buttons
        self.btn_segment = QPushButton("Run Instance Segmentation")
        self.btn_preview_segment = QPushButton("Preview Segmentation")
        self.btn_preview_segment.setToolTip("Segment the first 5 frames")
        btn_track = QPushButton("Run Tracking")
        btn_adjust_seg_ids = QPushButton("Harmonize segmentation colors")
        btn_adjust_seg_ids.setToolTip("WARNING: This will take a while")

        self.btn_segment.setEnabled(False)
        self.btn_preview_segment.setEnabled(False)

        self.btn_segment.clicked.connect(self._run_segmentation)
        self.btn_preview_segment.clicked.connect(self._run_demo_segmentation)
        btn_track.clicked.connect(self._run_tracking)
        btn_adjust_seg_ids.clicked.connect(self._adjust_ids)

        # Comboboxes
        self.combobox_segmentation = QComboBox()
        self.combobox_segmentation.addItem("select model")
        self.read_models()
        self.combobox_segmentation.currentTextChanged.connect(
            self.toggle_segmentation_buttons
        )

        # Horizontal lines
        line = QWidget()
        line.setFixedHeight(4)
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        line.setStyleSheet("background-color: #c0c0c0")

        ### Organize objects via widgets
        content = QWidget()
        content.setLayout(QVBoxLayout())

        content.layout().addWidget(label_segmentation)
        content.layout().addWidget(self.combobox_segmentation)
        content.layout().addWidget(self.btn_preview_segment)
        content.layout().addWidget(self.btn_segment)
        content.layout().addWidget(line)
        content.layout().addWidget(label_tracking)
        content.layout().addWidget(btn_track)
        content.layout().addWidget(btn_adjust_seg_ids)

        self.layout().addWidget(content)

    def toggle_segmentation_buttons(self, text):  # ?? copilot doc
        """
        Toggles the segmentation buttons based on the selected model.

        Args:
            text (str): The selected model. # ?? sieht schon falsch aus hier

        Returns:
            None
        """
        if text == "select model":
            self.btn_segment.setEnabled(False)
            self.btn_preview_segment.setEnabled(False)
        else:
            self.btn_segment.setEnabled(True)
            self.btn_preview_segment.setEnabled(True)

    def read_models(self):  # cpoilot doc
        """
        Reads the available models from the 'models' directory and adds them to the segmentation combobox.
        """
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        for file in os.listdir(path):
            self.combobox_segmentation.addItem(file)

    def _add_segmentation_to_viewer(self, mask):
        """
        Adds the segmentation as a layer to the viewer with a specified name

        Parameters
        ----------
        mask : array
            the segmentation data to add to the viewer
        """
        labels = self.viewer.add_labels(mask, name="calculated segmentation")
        self.parent.combobox_segmentation.setCurrentText(labels.name)
        print("Added segmentation to viewer")

    @napari.Viewer.bind_key("Shift-s")
    def _hotkey_run_segmentation(
        self,
    ):  # ?? interpretiere ich richtig, dass hier die Segmentierung über nen Hotkey ausgelöst wird? Falls ja: Den können wir entfernen, oder?
        ProcessingWindow.dock._run_segmentation()

    def _run_segmentation(self):
        """
        Calls segmentation without demo flag set
        """
        print("Calling full segmentation")

        worker = self._segment_image()
        worker.returned.connect(self._add_segmentation_to_viewer)
        # worker.errored.connect(handle_exception)
        # worker.start()

    def _run_demo_segmentation(self):
        """
        Calls segmentation with the demo flag set
        """
        print("Calling demo segmentation")

        worker = self._segment_image(True)
        worker.returned.connect(self._add_segmentation_to_viewer)
        # worker.errored.connect(handle_exception)
        # worker.start()

    @thread_worker(connect={"errored": handle_exception})
    def _segment_image(self, demo=False):
        """
        Run segmentation on the raw image data

        Parameters
        ----------
        demo : Boolean
            whether or not to do a demo of the segmentation
        Returns
        -------
        """
        print("Running segmentation")
        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:
            data = grab_layer(
                self.viewer, self.parent.combobox_image.currentText()
            ).data
        except ValueError as exc:
            handle_exception(exc)
            return

        if demo:
            data = data[0:5]

        selected_model = self.combobox_segmentation.currentText()

        parameters = self._get_parameters(selected_model)

        # set process limit
        if self.parent.rb_eco.isChecked():
            AMOUNT_OF_PROCESSES = np.maximum(1, int(multiprocessing.cpu_count() * 0.4))
        else:
            AMOUNT_OF_PROCESSES = np.maximum(1, int(multiprocessing.cpu_count() * 0.8))
        print("Running on {} processes max".format(AMOUNT_OF_PROCESSES))

        data_with_parameters = []
        for slice in data:
            data_with_parameters.append((slice, parameters))

        with Pool(AMOUNT_OF_PROCESSES) as p:
            mask = p.starmap(segment_slice, data_with_parameters)
            mask = np.asarray(mask)
            print("Done calculating segmentation")

        QApplication.restoreOverrideCursor()
        return mask

    def _get_parameters(self, model):
        """
        Get the parameters for the selected model

        Parameters
        ----------
        model : String
            The selected model

        Returns
        -------
        dict
            a dictionary of all the parameters based on selected model
        """
        print("Getting parameters")
        if model == "cellpose_neutrophils":
            print("Selected model 1")
            params = {
                "model_path": f"/models/{model}",
                "diameter": 15,
                "chan": 0,
                "chan2": 0,
                "flow_threshold": 0.4,
                "cellprob_threshold": 0,
            }
        params["model_path"] = os.path.dirname(__file__) + params["model_path"]

        return params

    def _add_tracks_to_viewer(self, params):
        """
        Adds the tracks as a layer to the viewer with a specified name

        Parameters
        ----------
        tracks : array
            the tracks data to add to the viewer
        """
        # check if tracks are usable
        tracks, layername = params
        try:
            tracks_layer = grab_layer(
                self.viewer, self.parent.combobox_tracks.currentText()
            )
        except ValueError as exc:
            if str(exc) == "Layer name can not be blank":
                self.viewer.add_tracks(tracks, name=layername)
            else:
                handle_exception(exc)
                return
        else:
            tracks_layer.data = tracks
        self.parent.combobox_tracks.setCurrentText(layername)
        print("Added tracks to viewer")

    def _run_tracking(self):
        """
        Calls the tracking function
        """
        print("Calling tracking")

        def on_yielded(value):
            if value == "Replace tracks layer":
                ret = choice_dialog(
                    "Tracks layer found. Do you want to replace it?",
                    [QMessageBox.Yes, QMessageBox.No],
                )
                if ret == 16384:
                    self.ret = ret
                    self.choice_event.set()
                else:
                    worker.quit()

        worker = self._track_segmentation()
        worker.returned.connect(self._add_tracks_to_viewer)
        worker.yielded.connect(on_yielded)

    @thread_worker(connect={"errored": handle_exception})
    def _track_segmentation(self):
        """
        Run tracking on the segmented data
        """
        print("Running tracking")
        QApplication.setOverrideCursor(Qt.WaitCursor)

        # get segmentation data
        try:
            data = grab_layer(
                self.viewer, self.parent.combobox_segmentation.currentText()
            ).data
        except ValueError as exc:
            handle_exception(exc)
            return

        # check for tracks layer
        try:
            tracks_layer = grab_layer(
                self.viewer, self.parent.combobox_tracks.currentText()
            )
        except ValueError:
            tracks_name = "Tracks"
        else:
            QApplication.restoreOverrideCursor()
            yield "Replace tracks layer"
            self.choice_event.wait()
            self.choice_event.clear()
            ret = self.ret
            del self.ret
            print(ret)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            if ret == 65536:
                QApplication.restoreOverrideCursor()
                return
            tracks_name = tracks_layer.name

        # set process limit
        if self.parent.rb_eco.isChecked():
            AMOUNT_OF_PROCESSES = np.maximum(1, int(multiprocessing.cpu_count() * 0.4))
        else:
            AMOUNT_OF_PROCESSES = np.maximum(1, int(multiprocessing.cpu_count() * 0.8))
        print("Running on {} processes max".format(AMOUNT_OF_PROCESSES))

        with Pool(AMOUNT_OF_PROCESSES) as p:
            extended_centroids = p.map(calculate_centroids, data)

        # calculate connections between centroids of adjacent slices

        slice_pairs = []
        for i in range(1, len(data)):
            slice_pairs.append((extended_centroids[i - 1], extended_centroids[i]))

        with Pool(AMOUNT_OF_PROCESSES) as p:
            matches = p.map(match_centroids, slice_pairs)

        tracks = np.array([])
        next_id = 0
        visited = []
        for i in range(len(matches)):
            visited.append([0] * len(matches[i]))

        for i in range(len(visited)):           # ?? hier wären Kommentare hilfreich
            for j in range(len(visited[i])):
                if visited[i][j]:
                    continue
                entry = [
                    next_id,
                    i,
                    int(matches[i][j][0][0][0]),
                    int(matches[i][j][0][0][1]),
                ]
                try:
                    tracks = np.append(tracks, np.array([entry]), axis=0)
                except:
                    tracks = np.array([entry])
                entry = [
                    next_id,
                    i + 1,
                    int(matches[i][j][1][0][0]),
                    int(matches[i][j][1][0][1]),
                ]
                tracks = np.append(tracks, np.array([entry]), axis=0)
                visited[i][j] = 1
                label = matches[i][j][1][1]

                slice = i + 1
                while True:
                    if slice >= len(matches):
                        break
                    labels = []
                    for k in range(len(matches[slice])):
                        labels.append(matches[slice][k][0][1])
                        visited[slice][k] = 1

                    if not label in labels:
                        break
                    match_number = labels.index(label)
                    entry = [
                        next_id,
                        slice + 1,
                        matches[slice][match_number][1][0][0],
                        matches[slice][match_number][1][0][1],
                    ]
                    tracks = np.append(tracks, np.array([entry]), axis=0)
                    label = matches[slice][match_number][1][1]

                    slice += 1

                next_id += 1

        tracks = tracks.astype(int)
        # np.save("tracks.npy", tracks) # TODO: why is this here?   # ?? Also für das originale Tracking haben wir das gespeichert und in einem anderen Skript eingelesen
                                                                    # ich vermute, das ist einfach ein Überbleibsel davon und kann weg oder?

        QApplication.restoreOverrideCursor()
        return tracks, tracks_name

    def _adjust_ids(self):  # ?? ich weiß, ist noch zu tun, aber ich vermute stark, dass dann Kommentare hilfreich sein werden :D
        """
        Replaces track ID 0. Also adjusts segmentation IDs to match track IDs
        """
        raise NotImplementedError("Not implemented yet!")
        print("Adjusting segmentation IDs")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        import sys

        np.set_printoptions(threshold=sys.maxsize)
        print(self.viewer.layers[self.viewer.layers.index("Tracks")].data)
        QApplication.restoreOverrideCursor()


def segment_slice(slice, parameters):
    """
    Calculate segmentation for a single slice

    Parameters
    ----------
    slice : napari
        the slice of raw image data to calculate segmentation for
    parameters : dict
        the parameters for the segmentation model

    Returns
    -------
    """
    model = models.CellposeModel(gpu=False, pretrained_model=parameters["model_path"])
    mask, _, _ = model.eval(
        slice,
        channels=[parameters["chan"], parameters["chan2"]],
        diameter=parameters["diameter"],
        flow_threshold=parameters["flow_threshold"],
        cellprob_threshold=parameters["cellprob_threshold"],
    )
    return mask


# calculate centroids
def calculate_centroids(slice):  # ?? copilot   # ?? Vlt. können wir hier im Namen klar machen, dass es nicht alle Centroids sind. Ich habe da aber grad nichts passendes parat
                                                # Falls du da auch keine gute Idee hast, können wir das aber auch einfach so lassen, ist nur ne mini Anmerkung
    """
    Calculate the centroids of objects in a 2D slice.

    Parameters
    ----------
    slice : numpy.ndarray
        A 2D numpy array representing the slice.

    Returns
    -------
    tuple
        A tuple containing two numpy arrays: the centroids and the labels.
    """
    labels = np.unique(slice)[1:]
    centroids = ndimage.center_of_mass(slice, labels=slice, index=labels)

    return (centroids, labels)


def match_centroids(slice_pair):    # ?? copilot docs; Konstanten raus; Was mir generell noch auffällt: Wäre es "optisch ansprechender", wenn wir die Funktion hier vor 
                                    # _track_segmentation definieren würden, da match_centroids dort aufgerufen wird? Wäre das "best practice" oder ist sowas egal?
                                    # und slice_pair sollten wir sehr gut kommentieren, da es nicht offensichtlich ist, was das ist bzw. was da drin steckt
    """
    Match centroids between two slices and return the matched pairs.

    Args:
        slice_pair (tuple): A tuple containing two slices, each represented by a tuple
                            containing the centroids and IDs of cells in that slice.

    Returns:
        list: A list of matched pairs, where each pair consists of the centroid and ID
              of a cell in the parent slice and the centroid and ID of the corresponding
              cell in the child slice.
    """
    APPROX_INF = 65535
    MAX_MATCHING_DIST = 45

    parent_centroids = slice_pair[0][0]         # ?? ich hab das hier mal nach vorne gezogen und alle folgenden Aufrufe von slice_pair auf die Variablen hier umgestellt
    parent_ids = slice_pair[0][1]
    child_centroids = slice_pair[1][0]
    child_ids = slice_pair[1][1]    

    num_cells_parent = len(parent_centroids)
    num_cells_child = len(child_centroids)



    # calculate distance between each pair of cells
    cost_mat = spatial.distance.cdist(parent_centroids, child_centroids)

    # if the distance is too far, change to approx. Inf.
    cost_mat[cost_mat > MAX_MATCHING_DIST] = APPROX_INF

    # add edges from cells in previous frame to auxillary vertices
    # in order to accomendate segmentation errors and leaving cells
    cost_mat_aug = (
        MAX_MATCHING_DIST
        * 1.2
        * np.ones((num_cells_parent, num_cells_child + num_cells_parent), dtype=float)
    )
    cost_mat_aug[:num_cells_parent, :num_cells_child] = cost_mat[:, :]

    # solve the optimization problem
    row_ind, col_ind = optimize.linear_sum_assignment(cost_mat_aug)

    matched_pairs = []



    for i in range(len(row_ind)):
        parent_centroid = np.around(parent_centroids[row_ind[i]])
        parent_id = parent_ids[row_ind[i]]
        try:
            child_centroid = np.around(child_centroids[col_ind[i]])
            child_id = child_ids[col_ind[i]]
        except:
            continue

        matched_pairs.append(([parent_centroid, parent_id], [child_centroid, child_id]))

    return matched_pairs
