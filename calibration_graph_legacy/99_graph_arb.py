# %%
from pathlib import Path
from typing import List
from qualibrate.orchestration.basic_orchestrator import BasicOrchestrator
from qualibrate.parameters import GraphParameters
from qualibrate.qualibration_graph import QualibrationGraph
from qualibrate.qualibration_library import QualibrationLibrary

library = QualibrationLibrary.get_active_library()


class Parameters(GraphParameters):
    qubits: List[str] = ["qubitC1", "qubitC2", "qubitC3"]



multiplexed = True
flux_point = "joint"
reset_type_thermal_or_active = "active"



g = QualibrationGraph(
    name="cal_graph",
    parameters=Parameters(),
    nodes={
        "resonator_spectroscopy": library.nodes["02a_Resonator_Spectroscopy"].copy(multiplexed=multiplexed, 
                                                                                   name="resonator_spectroscopy"),
        "resonator_spec_vs_flux": library.nodes["02b_Resonator_Spectroscopy_vs_Flux"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                           name="resonator_spec_vs_flux"),
        "resonator_spec_vs_flux2": library.nodes["02b_Resonator_Spectroscopy_vs_Flux"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                            name="resonator_spec_vs_flux2"),
        "qubit_spectroscopy": library.nodes["03a_Qubit_Spectroscopy"].copy(flux_point_joint_or_independent=flux_point,
                                                                           multiplexed=multiplexed,
                                                                           name="qubit_spectroscopy"),
        "qubit_spec_vs_flux": library.nodes["03b_Qubit_Spectroscopy_vs_Flux"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                   multiplexed=multiplexed, 
                                                                                   name="qubit_spec_vs_flux"),
        "power_rabi": library.nodes["04_Power_Rabi"].copy(flux_point_joint_or_independent=flux_point, 
                                                          multiplexed=multiplexed, 
                                                          name="power_rabi"),
        "power_rabi2": library.nodes["04_Power_Rabi"].copy(flux_point_joint_or_independent=flux_point, 
                                                           multiplexed=multiplexed, 
                                                           name="power_rabi2"),
        "ramsey": library.nodes["06_Ramsey"].copy(flux_point_joint_or_independent=flux_point, 
                                                  multiplexed=multiplexed, 
                                                  name="ramsey"),
        "readout_frequency_optimization": library.nodes["07a_Readout_Frequency_Optimization"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                                   multiplexed=multiplexed, 
                                                                                                   name="readout_frequency_optimization"),
        "IQ_blobs": library.nodes["07b_IQ_Blobs"].copy(flux_point_joint_or_independent=flux_point, 
                                                       multiplexed=multiplexed, 
                                                       name="IQ_blobs"),
        # "readout_power_optimization": library.nodes["07c_Readout_Power_Optimization"].copy(flux_point_joint_or_independent=flux_point, multiplexed=multiplexed, name="readout_power_optimization"),
        "ramsey_flux_calibration": library.nodes["08_Ramsey_vs_Flux_Calibration"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                       multiplexed=multiplexed, 
                                                                                       name="ramsey_flux_calibration"),
        "drag_calibration": library.nodes["09b_DRAG_Calibration_180_minus_180"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                     multiplexed=multiplexed, 
                                                                                     name="drag_calibration"),
        "power_rabi_error_amplification_x180": library.nodes["04_Power_Rabi"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                   multiplexed=multiplexed, operation_x180_or_any_90 = "x180", 
                                                                                   min_amp_factor = 0.9, max_amp_factor = 1.1, amp_factor_step = 0.005,
                                                                                   max_number_rabi_pulses_per_sweep = 200, reset_type_thermal_or_active = reset_type_thermal_or_active, 
                                                                                   state_discrimination = True, update_x90 = True,
                                                                                   name="power_rabi_error_amplification_x180"),
        "power_rabi_error_amplification_x90": library.nodes["04_Power_Rabi"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                  multiplexed=multiplexed, operation_x180_or_any_90 = "x90", 
                                                                                   min_amp_factor = 0.95, max_amp_factor = 1.05, amp_factor_step = 0.005,
                                                                                   max_number_rabi_pulses_per_sweep = 200, reset_type_thermal_or_active = reset_type_thermal_or_active, 
                                                                                   state_discrimination = True, update_x90 = True,
                                                                                   name="power_rabi_error_amplification_x90"),
        "single_qubit_randomized_benchmarking": library.nodes["10a_Single_Qubit_Randomized_Benchmarking"].copy(flux_point_joint_or_independent=flux_point, 
                                                                                                               multiplexed=multiplexed, 
                                                                                                               name="single_qubit_randomized_benchmarking"),
    },
    connectivity=[
                  ("resonator_spectroscopy", "resonator_spec_vs_flux"),
                  ("resonator_spec_vs_flux", "resonator_spec_vs_flux2"),
                  ("resonator_spec_vs_flux2", "qubit_spectroscopy"),
                  ("qubit_spectroscopy", "qubit_spec_vs_flux"),
                  ("qubit_spec_vs_flux", "power_rabi"),
                  ("power_rabi", "power_rabi2"),
                  ("power_rabi2", "ramsey"),
                  ("ramsey", "readout_frequency_optimization"),
                  ("readout_frequency_optimization", "IQ_blobs"),
                  ("IQ_blobs", "ramsey_flux_calibration"),
                  ("ramsey_flux_calibration", "drag_calibration"),
                  ("drag_calibration", "power_rabi_error_amplification_x180"),
                  ("power_rabi_error_amplification_x180", "power_rabi_error_amplification_x90"),
                  ("power_rabi_error_amplification_x90", "single_qubit_randomized_benchmarking"),
                  ],

    orchestrator=BasicOrchestrator(skip_failed=True),
)
# %%

g.run()
# %%
