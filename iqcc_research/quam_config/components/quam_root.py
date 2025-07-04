import os
import warnings
from pathlib import Path
from quam.core import QuamRoot, quam_dataclass
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

__all__ = ["Quam", "FEMQuAM", "OPXPlusQuAM"]


@quam_dataclass
class Quam(QuamRoot):
    """Example Quam root component."""

    octaves: Dict[str, Octave] = field(default_factory=dict)

    qubits: Dict[str, Transmon] = field(default_factory=dict)
    qubit_pairs: Dict[str, TransmonPair] = field(default_factory=dict)
    wiring: dict = field(default_factory=dict)
    network: dict = field(default_factory=dict)

    active_qubit_names: List[str] = field(default_factory=list)
    active_qubit_pair_names: List[str] = field(default_factory=list)

    _data_handler: ClassVar[DataHandler | None] = None
    qmm: ClassVar[QuantumMachinesManager | None] = None

    @classmethod
    def get_serialiser(cls) -> JSONSerialiser:
        """Get the serialiser for the QuamRoot class, which is the JSONSerialiser.

        This method can be overridden by subclasses to provide a custom serialiser.
        """
        return JSONSerialiser(
            content_mapping={"wiring": "wiring.json", "network": "wiring.json"}
        )
    
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

    @property
    def active_qubits(self) -> List[Transmon]:
        """Return the list of active qubits."""
        return [self.qubits[q] for q in self.active_qubit_names]

    @property
    def active_qubit_pairs(self) -> List[TransmonPair]:
        """Return the list of active qubits."""
        return [self.qubit_pairs[q] for q in self.active_qubit_pair_names]

    @property
    def depletion_time(self) -> int:
        """Return the longest depletion time amongst the active qubits."""
        return max(q.resonator.depletion_time for q in self.active_qubits)

    @property
    def thermalization_time(self) -> int:
        """Return the longest thermalization time amongst the active qubits."""
        return max(q.thermalization_time for q in self.active_qubits)

    def apply_all_couplers_to_min(self) -> None:
        """Apply the offsets that bring all the active qubit pairs to a decoupled point."""
        for qp in self.active_qubit_pairs:
            if qp.coupler is not None:
                qp.coupler.to_decouple_idle()

    def apply_all_flux_to_joint_idle(self) -> None:
        """Apply the offsets that bring all the active qubits to the joint sweet spot."""
        for q in self.active_qubits:
            if q.z is not None:
                q.z.to_joint_idle()
            else:
                warnings.warn(f"Didn't find z-element on qubit {q.name}, didn't set to joint-idle")
        for q in self.qubits:
            if self.qubits[q] not in self.active_qubits:
                if self.qubits[q].z is not None:
                    self.qubits[q].z.to_min()
                else:
                    warnings.warn(f"Didn't find z-element on qubit {q}, didn't set to min")
        self.apply_all_couplers_to_min()

    def apply_all_flux_to_min(self) -> None:
        """Apply the offsets that bring all the active qubits to the minimum frequency point."""
        for q in self.qubits:
            if self.qubits[q].z is not None:
                self.qubits[q].z.to_min()
            else:
                warnings.warn(f"Didn't find z-element on qubit {q}, didn't set to min")
        self.apply_all_couplers_to_min()

    def apply_all_flux_to_zero(self) -> None:
        """Apply the offsets that bring all the active qubits to the zero bias point."""
        for q in self.active_qubits:
            q.z.to_zero()
        
        
    def set_all_fluxes(self, flux_point : str, target : Union[Transmon, TransmonPair], do_align: bool = True) -> float:
        if flux_point == "independent":
            assert isinstance(target, Transmon), "Independent flux point is only supported for individual transmons"
        elif flux_point == "pairwise":
            assert isinstance(target, TransmonPair), "Pairwise flux point is only supported for transmon pairs"
        
        if flux_point == "joint":
            self.apply_all_flux_to_joint_idle()
            if isinstance(target, TransmonPair):
                target_bias =target.mutual_flux_bias
            else:
                target_bias = target.z.joint_offset
        else:
            self.apply_all_flux_to_min()
        
        if flux_point == "independent":
            target.z.to_independent_idle()
            target_bias = target.z.independent_offset
            
        elif flux_point == "pairwise":
            target.to_mutual_idle()
            target_bias = target.mutual_flux_bias
        
        if isinstance(target, Transmon):
            target.z.settle()
        elif isinstance(target, TransmonPair):
            target.qubit_control.z.settle()
            target.qubit_target.z.settle()
        
        if do_align:
            target.align()
            
        return target_bias      

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
