import os
import warnings
from pathlib import Path
from quam.core import quam_dataclass
from quam.components.octave import Octave
from quam.components.ports import (
    LFFEMAnalogOutputPort,
    LFFEMAnalogInputPort,
    FEMDigitalOutputPort,
    OPXPlusAnalogOutputPort,
    OPXPlusAnalogInputPort,
    OPXPlusDigitalOutputPort,
    FEMPortsContainer,
    OPXPlusPortsContainer,
)
from quam.serialisation.json import JSONSerialiser

from .transmon import Transmon
from .transmon_pair import TransmonPair

from qm import QuantumMachinesManager, QuantumMachine
from qualang_tools.results.data_handler import DataHandler

from dataclasses import field
from typing import List, Dict, ClassVar, Any, Optional, Sequence, Union
from ..cloud_infrastructure import CloudQuantumMachinesManager

from quam_builder.architecture.superconducting.qpu import FluxTunableQuam

__all__ = ["Quam", "FEMQuAM", "OPXPlusQuAM"]


@quam_dataclass
class Quam(FluxTunableQuam):
    """Example Quam root component with enhanced functionality."""

    _data_handler: ClassVar[DataHandler | None] = None

    @classmethod
    def load(cls, *args, **kwargs) -> "Quam":
        if not args:
            if "QUAM_STATE_PATH" in os.environ:
                args = (os.environ["QUAM_STATE_PATH"],)
            else:
                raise ValueError(
                    "No path argument provided to load the QuAM state. "
                    "Please provide a path or set the 'QUAM_STATE_PATH' environment variable. "
                    "See the README for instructions."
                )
        return super().load(*args, **kwargs)

    def save(
        self,
        path: Union[Path, str] | None = None,
        content_mapping: Dict[Union[Path, str], Sequence[str]] | None = None,
        include_defaults: bool = False,
        ignore: Sequence[str] | None = None,
    ):
        if path is None and "QUAM_STATE_PATH" in os.environ:
            path = os.environ["QUAM_STATE_PATH"]

        super().save(path, content_mapping, include_defaults, ignore)

    @property
    def data_handler(self) -> DataHandler:
        """Return the existing data handler or open a new one to conveniently handle data saving."""
        if self._data_handler is None:
            self._data_handler = DataHandler(root_data_folder=self.network["data_folder"])
            DataHandler.node_data = {"quam": "./state.json"}
        return self._data_handler

    def connect(self) -> QuantumMachinesManager:
        """Open a Quantum Machine Manager with the credentials ("host" and "cluster_name") as defined in the network file.

        Returns: the opened Quantum Machine Manager.
        """
        if self.network.get("cloud", False):
            self.qmm = CloudQuantumMachinesManager(self.network["quantum_computer_backend"])
        else:
            settings = dict(
                host=self.network["host"],
                cluster_name=self.network["cluster_name"]
            )

            if "port" in self.network:
                settings["port"] = self.network["port"]

            self.qmm = QuantumMachinesManager(**settings)

        return self.qmm

    def get_octave_config(self) -> dict:
        """Return the Octave configuration."""
        octave_config = None
        for octave in self.octaves.values():
            if octave_config is None:
                octave_config = octave.get_octave_config()
            else:
                octave_config.add_device_info(octave.name, octave.ip, octave.port)

        return octave_config

    def calibrate_octave_ports(self, QM: QuantumMachine) -> None:
        """Calibrate the Octave ports for all the active qubits.

        Args:
            QM (QuantumMachine): the running quantum machine.
        """
        from qm.octave.octave_mixer_calibration import NoCalibrationElements

        for name in self.active_qubit_names:
            try:
                self.qubits[name].calibrate_octave(QM)
            except NoCalibrationElements:
                print(f"No calibration elements found for {name}. Skipping calibration.")


@quam_dataclass
class FEMQuAM(Quam):
    ports: FEMPortsContainer = field(default_factory=FEMPortsContainer)


@quam_dataclass
class OPXPlusQuAM(Quam):
    ports: OPXPlusPortsContainer = field(default_factory=OPXPlusPortsContainer)
